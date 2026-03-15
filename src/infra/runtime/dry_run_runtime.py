"""
src/infra/runtime/dry_run_runtime.py — SimulatedAgentRuntime for CI/tests.

Deterministic stub for CI/acceptance tests.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Iterator

import structlog

from src.core.models import AgentExecutionResult, AgentProps, ExecutionContext
from src.core.ports import AgentRuntimePort, SessionHandle

log = structlog.get_logger(__name__)


class SimulatedSessionHandle(SessionHandle):
    def __init__(self, session_id: str) -> None:
        self._session_id = session_id
        self.context: ExecutionContext | None = None
        self.start_time = time.monotonic()

    @property
    def session_id(self) -> str:
        return self._session_id


class SimulatedAgentRuntime(AgentRuntimePort):
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
    ) -> SimulatedSessionHandle:
        session_id = f"simulated-{agent_props.agent_id}-{int(time.time() * 1000)}"
        log.info("simulated.session_started", session_id=session_id)
        return SimulatedSessionHandle(session_id)

    def send_execution_payload(
        self,
        handle: SimulatedSessionHandle,
        context: ExecutionContext,
    ) -> None:
        handle.context = context

    def wait_for_completion(
        self,
        handle: SimulatedSessionHandle,
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
                    f"# Simulated stub for task {context.task_id}\n"
                    f"# File: {rel_path}\n"
                    "pass\n"
                )
                modified.append(rel_path)

        elapsed = time.monotonic() - handle.start_time
        return AgentExecutionResult(
            success=True,
            exit_code=0,
            modified_files=modified,
            stdout=f"[simulated] Task {getattr(context, 'task_id', '?')} complete\n",
            stderr="",
            elapsed_seconds=elapsed,
        )

    def terminate_session(self, handle: SimulatedSessionHandle) -> None:
        pass

    def stream_logs(self, handle: SimulatedSessionHandle) -> Iterator[str]:
        yield "[simulated] no live logs\n"
