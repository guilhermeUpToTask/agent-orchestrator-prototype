"""
src/infra/runtime/pi_runtime.py — AgentRuntimePort adapter for pi-mono CLI.

Runs `pi -p "<prompt>"` in the task workspace.

API key resolution is the responsibility of OrchestratorConfig + the factory.
This class receives an already-resolved key and a backend name that tells it
which environment variable pi expects the key under.

Supported runtime_config keys in AgentProps:
  model       — model ID passed to pi (default: "claude-sonnet-4-5")
  backend     — "anthropic" (default) | "gemini" | "openrouter"
                Selects which API key the factory pulls from OrchestratorConfig
                and which env var it is injected under for the pi process.
  extra_flags — list of additional CLI flags passed verbatim to pi
                (e.g. ["-e", "~/.pi/agent/extensions/llm-traffic-logger.ts"])

Backend → env var mapping (matches pi's internal env-api-keys.ts):
  anthropic  → ANTHROPIC_API_KEY
  gemini     → GEMINI_API_KEY
  openrouter → OPENROUTER_API_KEY

Example AgentProps.runtime_config:
  { "model": "claude-sonnet-4-5" }
  { "model": "gemini-2.0-flash", "backend": "gemini" }
  { "model": "anthropic/claude-sonnet-4-5", "backend": "openrouter" }
  { "model": "openai/gpt-4o", "backend": "openrouter" }
"""
from __future__ import annotations

import os
from typing import Literal

import structlog

from src.domain import ExecutionContext
from src.infra.runtime.agent_runtime import CliAgentRuntime, CliSessionHandle

log = structlog.get_logger(__name__)

Backend = Literal["anthropic", "gemini", "openrouter"]

# Maps backend name → the env var name pi reads for that provider.
# Source: pi-mono/packages/ai/src/env-api-keys.ts
_BACKEND_ENV_VAR: dict[str, str] = {
    "anthropic":  "ANTHROPIC_API_KEY",
    "gemini":     "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


class PiAgentRuntime(CliAgentRuntime):
    """
    Executes tasks using the pi-mono coding agent CLI (`pi` command).

    Pi is a CLI coding agent that runs autonomously in a workspace directory.
    It respects AGENTS.md / CLAUDE.md project instructions and supports
    loading TypeScript extensions via the -e flag.

    The prompt is rendered in rich Markdown since pi passes it directly to
    the underlying LLM with full workspace context already available.
    """

    DEFAULT_MODEL = "claude-sonnet-4-5"
    DEFAULT_BACKEND: Backend = "anthropic"
    VALID_BACKENDS = frozenset(_BACKEND_ENV_VAR.keys())

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        extra_flags: list[str] | None = None,
        backend: Backend = DEFAULT_BACKEND,
    ) -> None:
        if backend not in self.VALID_BACKENDS:
            raise ValueError(
                f"Invalid backend '{backend}'. Valid values: {', '.join(sorted(self.VALID_BACKENDS))}"
            )
        if not api_key:
            env_var = _BACKEND_ENV_VAR[backend]
            raise ValueError(
                f"PiAgentRuntime requires a non-empty api_key for backend '{backend}'. "
                f"Set {env_var} in your environment or .env file."
            )
        super().__init__(api_key, model, extra_flags)
        self._backend = backend
        self._env_var = _BACKEND_ENV_VAR[backend]

    @property
    def log_prefix(self) -> str:
        return "pi"

    def _get_env(self) -> dict[str, str]:
        # Inject the API key under the env var pi expects for this backend.
        # This matches pi's internal env-api-keys.ts mapping.
        return {
            **os.environ,
            self._env_var: self._api_key,
        }

    def _build_cmd(self, handle: CliSessionHandle) -> list[str]:
        cmd = ["pi"]

        # Model override — only pass if non-default to keep CLI output clean
        if self._model != self.DEFAULT_MODEL:
            cmd += ["--model", self._model]

        # One-shot prompt mode
        cmd += ["-p", handle.prompt]

        # Any caller-supplied flags (e.g. -e path/to/extension.ts)
        cmd += self._extra_flags

        log.info(
            "pi.building_cmd",
            model=self._model,
            flags=self._extra_flags,
            session_id=handle.session_id,
        )
        return cmd

    def _build_prompt(self, context: ExecutionContext) -> str:
        """
        Build a Markdown prompt for pi.

        Pi passes the prompt to the underlying LLM with the full workspace
        context already available, so the prompt focuses on the task contract:
        what to do, which files to touch, and how success is measured.

        Project-level conventions (coding style, architecture rules) should
        live in the workspace AGENTS.md — not in this prompt — so that they
        are applied consistently across all agents regardless of runtime.
        """
        allowed = "\n".join(f"  - {f}" for f in context.allowed_files) \
                  or "  - any files in the workspace"

        criteria = ""
        if context.execution.acceptance_criteria:
            items = "\n".join(f"- {c}" for c in context.execution.acceptance_criteria)
            criteria = f"\n\n## Acceptance criteria\n{items}"

        test_note = ""
        if context.execution.test_command:
            test_note = (
                f"\n\n## Verification\n"
                f"Run `{context.execution.test_command}` to verify your work.\n"
                f"All tests must pass before you consider the task complete."
            )

        constraints = ""
        if context.execution.constraints:
            items = "\n".join(f"- **{k}**: {v}" for k, v in context.execution.constraints.items())
            constraints = f"\n\n## Constraints\n{items}"

        metadata = ""
        if context.metadata:
            items = "\n".join(f"- {k}: {v}" for k, v in context.metadata.items())
            metadata = f"\n\n## Metadata\n{items}"

        return (
            f"# Task: {context.title}\n\n"
            f"{context.description}\n\n"
            f"## Files you may modify\n{allowed}\n\n"
            f"**Do not modify any other files. "
            f"Do not create files outside the allowed list.**"
            f"{constraints}"
            f"{criteria}"
            f"{test_note}"
            f"{metadata}\n\n"
            f"---\n"
            f"Task ID: `{context.task_id}` | Branch: `{context.branch}`"
        )