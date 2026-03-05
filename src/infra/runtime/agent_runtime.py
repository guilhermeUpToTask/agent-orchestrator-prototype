"""
src/infra/runtime/agent_runtime.py — AgentRuntimePort adapters.

Two modes:
  SubprocessAgentRuntime  — launches a real agent CLI binary
  DryRunAgentRuntime      — deterministic stub for CI/tests; writes allowed files
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Iterator

import structlog

from src.core.models import AgentExecutionResult, AgentProps, ExecutionContext
from src.core.ports import AgentRuntimePort, SessionHandle

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Concrete SessionHandle implementations
# ---------------------------------------------------------------------------

class SubprocessSessionHandle(SessionHandle):
    def __init__(self, session_id: str, process: subprocess.Popen, context_file: str) -> None:
        self._session_id = session_id
        self.process = process
        self.context_file = context_file
        self.stdout_lines: list[str] = []
        self.stderr_lines: list[str] = []
        self.start_time = time.monotonic()

    @property
    def session_id(self) -> str:
        return self._session_id


class DryRunSessionHandle(SessionHandle):
    def __init__(self, session_id: str) -> None:
        self._session_id = session_id
        self.context: ExecutionContext | None = None
        self.start_time = time.monotonic()

    @property
    def session_id(self) -> str:
        return self._session_id


# ---------------------------------------------------------------------------
# Real subprocess adapter
# ---------------------------------------------------------------------------

class SubprocessAgentRuntime(AgentRuntimePort):
    """
    Launches an agent CLI as a subprocess with workspace as CWD.
    The agent binary is resolved from agent_props.tools or a default path.
    """

    def __init__(
        self,
        agent_binary: str = "/usr/local/bin/agent-cli",
        logs_base: str = "workflow/logs",
    ) -> None:
        self._binary = agent_binary
        self._logs_base = Path(logs_base)

    def start_session(
        self,
        agent_props: AgentProps,
        workspace_path: str,
        env_vars: dict[str, str],
    ) -> SubprocessSessionHandle:
        session_id = f"session-{agent_props.agent_id}-{int(time.time())}"
        env = {**os.environ, **env_vars, "AGENT_SESSION_ID": session_id}

        # Context file path — agent reads this on startup
        context_file = os.path.join(workspace_path, ".agent_context.json")

        process = subprocess.Popen(
            [self._binary, "run", "--context", context_file],
            cwd=workspace_path,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        log.info("runtime.session_started", session_id=session_id, pid=process.pid)
        return SubprocessSessionHandle(session_id, process, context_file)

    def send_execution_payload(
        self,
        handle: SubprocessSessionHandle,
        context: ExecutionContext,
    ) -> None:
        payload = context.model_dump(mode="json")
        Path(handle.context_file).write_text(json.dumps(payload, indent=2))

    def wait_for_completion(
        self,
        handle: SubprocessSessionHandle,
        timeout_seconds: int = 600,
    ) -> AgentExecutionResult:
        try:
            stdout, stderr = handle.process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            handle.process.kill()
            stdout, stderr = handle.process.communicate()
            elapsed = time.monotonic() - handle.start_time
            return AgentExecutionResult(
                success=False,
                exit_code=-1,
                stdout=stdout or "",
                stderr=f"TIMEOUT after {timeout_seconds}s\n" + (stderr or ""),
                elapsed_seconds=elapsed,
            )

        exit_code = handle.process.returncode
        elapsed = time.monotonic() - handle.start_time
        return AgentExecutionResult(
            success=exit_code == 0,
            exit_code=exit_code,
            stdout=stdout or "",
            stderr=stderr or "",
            elapsed_seconds=elapsed,
        )

    def terminate_session(self, handle: SubprocessSessionHandle) -> None:
        if handle.process.poll() is None:
            handle.process.send_signal(signal.SIGTERM)
            try:
                handle.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                handle.process.kill()

    def stream_logs(self, handle: SubprocessSessionHandle) -> Iterator[str]:
        if handle.process.stdout:
            for line in handle.process.stdout:
                yield line


# ---------------------------------------------------------------------------
# Dry-run adapter (deterministic, no subprocess)
# ---------------------------------------------------------------------------

class DryRunAgentRuntime(AgentRuntimePort):
    """
    Deterministic stub for CI/acceptance tests.

    Behaviour:
      - Creates allowed files with stub content
      - Always reports success
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
        pass  # nothing to terminate

    def stream_logs(self, handle: DryRunSessionHandle) -> Iterator[str]:
        yield "[dry-run] no live logs\n"
