"""
src/infra/runtime/claude_code_runtime.py — AgentRuntimePort adapter for Claude Code CLI.

Runs `claude --dangerously-skip-permissions -p "<prompt>"` in the task workspace.
Requires ANTHROPIC_API_KEY in the environment.
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


class ClaudeSessionHandle(SessionHandle):
    def __init__(self, session_id: str, workspace_path: str) -> None:
        self._session_id = session_id
        self.workspace_path = workspace_path
        self.prompt: str = ""
        self.context: ExecutionContext | None = None
        self.start_time = time.monotonic()

    @property
    def session_id(self) -> str:
        return self._session_id


class ClaudeCodeRuntime(AgentRuntimePort):
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
        self._api_key = api_key
        self._model = model
        self._extra_flags = extra_flags or []

    def start_session(
        self,
        agent_props: AgentProps,
        workspace_path: str,
        env_vars: dict[str, str],
    ) -> ClaudeSessionHandle:
        session_id = f"claude-{agent_props.agent_id}-{int(time.time())}"
        log.info("claude.session_started", session_id=session_id, workspace=workspace_path)
        return ClaudeSessionHandle(session_id, workspace_path)

    def send_execution_payload(
        self,
        handle: ClaudeSessionHandle,
        context: ExecutionContext,
    ) -> None:
        handle.context = context
        handle.prompt = self._build_prompt(context)
        log.info("claude.prompt_built", session_id=handle.session_id, task_id=context.task_id)

    def wait_for_completion(
        self,
        handle: ClaudeSessionHandle,
        timeout_seconds: int = 600,
    ) -> AgentExecutionResult:
        env = {
            **os.environ,
            "ANTHROPIC_API_KEY": self._api_key,
        }

        cmd = [
            "claude",
            "--dangerously-skip-permissions",
            "-p", handle.prompt,
        ]
        if self._model != self.DEFAULT_MODEL:
            cmd += ["--model", self._model]
        cmd += self._extra_flags

        log.info(
            "claude.running",
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
            log.error("claude.timeout", session_id=handle.session_id, timeout=timeout_seconds)
            return AgentExecutionResult(
                success=False,
                exit_code=-1,
                stdout=exc.stdout or "",
                stderr=f"TIMEOUT after {timeout_seconds}s\n{exc.stderr or ''}",
                elapsed_seconds=elapsed,
            )

        elapsed = time.monotonic() - handle.start_time
        log.info(
            "claude.finished",
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

    def terminate_session(self, handle: ClaudeSessionHandle) -> None:
        # Claude Code CLI is a single blocking subprocess call — nothing to terminate
        pass

    def stream_logs(self, handle: ClaudeSessionHandle) -> Iterator[str]:
        yield "[claude] no live log streaming — check stdout.txt after completion\n"

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _build_prompt(context: ExecutionContext) -> str:
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