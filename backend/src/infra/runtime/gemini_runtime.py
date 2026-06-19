"""
src/infra/runtime/gemini_runtime.py — AgentRuntimePort adapter for Gemini CLI.

Runs `gemini --yolo -p "<prompt>"` in the task workspace.
Requires GEMINI_API_KEY in the environment.
"""
from __future__ import annotations

import os

from src.domain import ExecutionContext
from src.infra.runtime.agent_runtime import CliAgentRuntime, CliSessionHandle
import structlog

log = structlog.get_logger(__name__)


class GeminiAgentRuntime(CliAgentRuntime):
    """
    Executes tasks using the Gemini CLI (`gemini` command).

    Supported runtime_config keys in AgentProps:
      model     — e.g. "gemini-2.0-flash" (default)
      extra_flags — list of additional CLI flags
    """

    DEFAULT_MODEL = "gemini-2.0-flash"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        extra_flags: list[str] | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required for GeminiAgentRuntime")
        super().__init__(api_key, model, extra_flags)

    @property
    def log_prefix(self) -> str:
        return "gemini"

    def _get_env(self) -> dict[str, str]:
        return {
            **os.environ,
            "GEMINI_API_KEY": self._api_key,
            "GEMINI_MODEL": self._model,
        }

    def _build_cmd(self, handle: CliSessionHandle) -> list[str]:
        cmd = [
            "gemini",
            f"--model={self._model}",  # explicit override; CLI default is often 2.5-flash
            "--yolo",
            "-p", handle.prompt,
        ]
        cmd += self._extra_flags
        log.info("gemini.building_cmd", model=self._model, flags=self._extra_flags)
        return cmd

    def _build_prompt(self, context: ExecutionContext) -> str:
        allowed = ", ".join(context.allowed_files) or "any files in the workspace"

        criteria = ""
        if context.execution.acceptance_criteria:
            items = "\n".join(f"  - {c}" for c in context.execution.acceptance_criteria)
            criteria = f"\n\nAcceptance criteria:\n{items}"

        test_note = ""
        if context.execution.test_command:
            test_note = f"\n\nVerify your work by running: {context.execution.test_command}"

        return (
            f"You are a software agent completing a development task.\n\n"
            f"Task ID: {context.task_id}\n"
            f"Title: {context.title}\n\n"
            f"Description:\n{context.description}\n\n"
            f"Files you are allowed to modify: {allowed}\n"
            f"Do not modify any other files."
            f"{criteria}"
            f"{test_note}"
        )