"""
src/infra/runtime/claude_code_runtime.py — AgentRuntimePort adapter for Claude Code CLI.

Runs `claude --dangerously-skip-permissions -p "<prompt>"` in the task workspace.
Requires ANTHROPIC_API_KEY in the environment.
"""
from __future__ import annotations

import os

from src.core.models import ExecutionContext
from src.infra.runtime.cli_agent_runtime import CliAgentRuntime, CliSessionHandle


class ClaudeCodeRuntime(CliAgentRuntime):
    """
    Executes tasks using the Claude Code CLI (`claude` command).

    Supported runtime_config keys in AgentProps:
      model       — e.g. "claude-sonnet-4-5" (default)
      extra_flags — list of additional CLI flags
    """

    DEFAULT_MODEL = "claude-sonnet-4-5"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        extra_flags: list[str] | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is required for ClaudeCodeRuntime")
        super().__init__(api_key, model, extra_flags)

    @property
    def log_prefix(self) -> str:
        return "claude"

    def _get_env(self) -> dict[str, str]:
        return {
            **os.environ,
            "ANTHROPIC_API_KEY": self._api_key,
        }

    def _build_cmd(self, handle: CliSessionHandle) -> list[str]:
        cmd = [
            "claude",
            "--dangerously-skip-permissions",
            "-p", handle.prompt,
        ]
        if self._model != self.DEFAULT_MODEL:
            cmd += ["--model", self._model]
        cmd += self._extra_flags
        return cmd

    def _build_prompt(self, context: ExecutionContext) -> str:
        # Claude Code understands richer markdown context
        allowed = "\n".join(f"  - {f}" for f in context.allowed_files) or "  - any files in the workspace"

        criteria = ""
        if context.execution.acceptance_criteria:
            items = "\n".join(f"- {c}" for c in context.execution.acceptance_criteria)
            criteria = f"\n\n## Acceptance criteria\n{items}"

        test_note = ""
        if context.execution.test_command:
            test_note = f"\n\n## Verification\nRun `{context.execution.test_command}` to verify your work."

        constraints = ""
        if context.execution.constraints:
            items = "\n".join(f"- {k}: {v}" for k, v in context.execution.constraints.items())
            constraints = f"\n\n## Constraints\n{items}"

        return (
            f"# Task: {context.title}\n\n"
            f"{context.description}\n\n"
            f"## Files you may modify\n{allowed}\n\n"
            f"**Do not modify any other files.**"
            f"{constraints}"
            f"{criteria}"
            f"{test_note}"
        )