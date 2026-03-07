"""
src/infra/runtime/agent_runtime.py — DryRunAgentRuntime for CI/tests.

Real agent runtimes live in their own modules:
  gemini_runtime.py      — Gemini CLI
  claude_code_runtime.py — Claude Code CLI
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Iterator

import structlog

from src.core.models import AgentExecutionResult, AgentProps, ExecutionContext
from src.core.ports import AgentRuntimePort, SessionHandle

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Dry-run session handle
# ---------------------------------------------------------------------------

class DryRunSessionHandle(SessionHandle):
    def __init__(self, session_id: str) -> None:
        self._session_id = session_id
        self.context: ExecutionContext | None = None
        self.start_time = time.monotonic()

    @property
    def session_id(self) -> str:
        return self._session_id


# ---------------------------------------------------------------------------
# Dry-run adapter (deterministic, no subprocess)
# ---------------------------------------------------------------------------

class DryRunAgentRuntime(AgentRuntimePort):
    """
    Deterministic stub for CI/acceptance tests.

    Behaviour:
      - Creates allowed files with stub content
      - Always reports success (unless simulate_failure=True)
      - Simulates short elapsed time
    """

    def __init__(self, simulate_failure: bool = False) -> None:
        self._simulate_failure = simulate_failure

    def start_session(
        self,
        agent_props: AgentProps,
        workspace_path: str,
        env_vars: dict[str, str],
    ) -> DryRunSessionHandle:
        session_id = f"dryrun-{agent_props.agent_id}-{int(time.time() * 1000)}"
        log.info("dryrun.session_started", session_id=session_id)
        return DryRunSessionHandle(session_id)

    def send_execution_payload(
        self,
        handle: DryRunSessionHandle,
        context: ExecutionContext,
    ) -> None:
        handle.context = context

    def wait_for_completion(
        self,
        handle: DryRunSessionHandle,
        timeout_seconds: int = 600,
    ) -> AgentExecutionResult:
        if self._simulate_failure:
            return AgentExecutionResult(
                success=False,
                exit_code=1,
                stdout="",
                stderr="Simulated failure",
                elapsed_seconds=0.1,
            )

        context = handle.context
        modified: list[str] = []

        if context:
            ws = Path(context.workspace_dir)
            for rel_path in context.allowed_files:
                full = ws / rel_path
                full.parent.mkdir(parents=True, exist_ok=True)
                full.write_text(
                    f"# Dry-run stub for task {context.task_id}\n"
                    f"# File: {rel_path}\n"
                    "pass\n"
                )
                modified.append(rel_path)

        elapsed = time.monotonic() - handle.start_time
        return AgentExecutionResult(
            success=True,
            exit_code=0,
            modified_files=modified,
            stdout=f"[dry-run] Task {getattr(context, 'task_id', '?')} complete\n",
            stderr="",
            elapsed_seconds=elapsed,
        )

    def terminate_session(self, handle: DryRunSessionHandle) -> None:
        pass

    def stream_logs(self, handle: DryRunSessionHandle) -> Iterator[str]:
        yield "[dry-run] no live logs\n"