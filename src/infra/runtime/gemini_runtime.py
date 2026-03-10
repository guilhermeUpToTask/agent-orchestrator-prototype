"""
src/infra/runtime/gemini_runtime.py — AgentRuntimePort adapter for Gemini CLI.

Runs `gemini --yolo -p "<prompt>"` in the task workspace.
Requires GEMINI_API_KEY in the environment.
"""
from __future__ import annotations

import os
import subprocess
import time
from typing import Iterator

import structlog

from src.core.models import AgentExecutionResult, AgentProps, ExecutionContext
from src.core.ports import AgentRuntimePort, SessionHandle

log = structlog.get_logger(__name__)


class GeminiSessionHandle(SessionHandle):
    def __init__(self, session_id: str, workspace_path: str) -> None:
        self._session_id = session_id
        self.workspace_path = workspace_path
        self.prompt: str = ""
        self.context: ExecutionContext | None = None
        self.start_time = time.monotonic()

    @property
    def session_id(self) -> str:
        return self._session_id


class GeminiAgentRuntime(AgentRuntimePort):
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
        self._api_key = api_key
        self._model = model
        self._extra_flags = extra_flags or []

    def start_session(
        self,
        agent_props: AgentProps,
        workspace_path: str,
        env_vars: dict[str, str],
    ) -> GeminiSessionHandle:
        session_id = f"gemini-{agent_props.agent_id}-{int(time.time())}"
        log.info("gemini.session_started", session_id=session_id, workspace=workspace_path)
        return GeminiSessionHandle(session_id, workspace_path)

    def send_execution_payload(
        self,
        handle: GeminiSessionHandle,
        context: ExecutionContext,
    ) -> None:
        handle.context = context
        handle.prompt = self._build_prompt(context)
        log.info("gemini.prompt_built", session_id=handle.session_id, task_id=context.task_id)

    def wait_for_completion(
        self,
        handle: GeminiSessionHandle,
        timeout_seconds: int = 600,
    ) -> AgentExecutionResult:
        env = {
            **os.environ,
            "GEMINI_API_KEY": self._api_key,
        }

        cmd = [
            "gemini",
            "--model", self._model,  # always explicit — CLI default is 2.5-pro, not 2.0-flash
            "--yolo",
            "-p", handle.prompt,
        ]
        cmd += self._extra_flags

        log.info(
            "gemini.running",
            session_id=handle.session_id,
            cwd=handle.workspace_path,
            model=self._model,
        )

        try:
            result = subprocess.run(
                cmd,
                cwd=handle.workspace_path,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            elapsed = time.monotonic() - handle.start_time
            log.error("gemini.timeout", session_id=handle.session_id, timeout=timeout_seconds)
            return AgentExecutionResult(
                success=False,
                exit_code=-1,
                stdout=exc.stdout or "",
                stderr=f"TIMEOUT after {timeout_seconds}s\n{exc.stderr or ''}",
                elapsed_seconds=elapsed,
            )

        elapsed = time.monotonic() - handle.start_time
        log.info(
            "gemini.finished",
            session_id=handle.session_id,
            exit_code=result.returncode,
            elapsed=round(elapsed, 2),
        )
        return AgentExecutionResult(
            success=result.returncode == 0,
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            elapsed_seconds=elapsed,
        )

    def terminate_session(self, handle: GeminiSessionHandle) -> None:
        # Gemini CLI is a single blocking subprocess call — nothing to terminate
        pass

    def stream_logs(self, handle: GeminiSessionHandle) -> Iterator[str]:
        # Logs are only available after wait_for_completion
        yield "[gemini] no live log streaming — check stdout.txt after completion\n"

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _build_prompt(context: ExecutionContext) -> str:
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