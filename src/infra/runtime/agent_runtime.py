"""
src/infra/runtime/agent_runtime.py — Base runtime implementation for CLI tools.
"""
from __future__ import annotations

import subprocess
import time
from abc import ABC, abstractmethod
from typing import Iterator

import structlog

from src.core.models import AgentExecutionResult, AgentProps, ExecutionContext
from src.core.ports import AgentRuntimePort, SessionHandle

log = structlog.get_logger(__name__)


class CliSessionHandle(SessionHandle):
    def __init__(self, session_id: str, workspace_path: str) -> None:
        self._session_id = session_id
        self.workspace_path = workspace_path
        self.prompt: str = ""
        self.context: ExecutionContext | None = None
        self.start_time = time.monotonic()

    @property
    def session_id(self) -> str:
        return self._session_id


class CliAgentRuntime(AgentRuntimePort, ABC):
    """
    Base class for agent runtimes that execute a CLI command in a subprocess.
    """
    def __init__(
        self,
        api_key: str,
        model: str,
        extra_flags: list[str] | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._extra_flags = extra_flags or []

    @property
    @abstractmethod
    def log_prefix(self) -> str:
        pass

    @abstractmethod
    def _build_cmd(self, handle: CliSessionHandle) -> list[str]:
        pass

    @abstractmethod
    def _get_env(self) -> dict[str, str]:
        pass

    @abstractmethod
    def _build_prompt(self, context: ExecutionContext) -> str:
        pass

    def start_session(
        self,
        agent_props: AgentProps,
        workspace_path: str,
        env_vars: dict[str, str],
    ) -> CliSessionHandle:
        session_id = f"{self.log_prefix}-{agent_props.agent_id}-{int(time.time())}"
        log.info(f"{self.log_prefix}.session_started", session_id=session_id, workspace=workspace_path)
        return CliSessionHandle(session_id, workspace_path)

    def send_execution_payload(
        self,
        handle: CliSessionHandle,
        context: ExecutionContext,
    ) -> None:
        # Type enforcement since we only ever get our own handle types
        if not isinstance(handle, CliSessionHandle):
            raise TypeError(f"Expected CliSessionHandle, got {type(handle)}")
        handle.context = context
        handle.prompt = self._build_prompt(context)
        log.info(f"{self.log_prefix}.prompt_built", session_id=handle.session_id, task_id=context.task_id)

    def wait_for_completion(
        self,
        handle: CliSessionHandle,
        timeout_seconds: int = 600,
    ) -> AgentExecutionResult:
        if not isinstance(handle, CliSessionHandle):
            raise TypeError(f"Expected CliSessionHandle, got {type(handle)}")
            
        env = self._get_env()
        cmd = self._build_cmd(handle)

        log.info(
            f"{self.log_prefix}.running",
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
            log.error(f"{self.log_prefix}.timeout", session_id=handle.session_id, timeout=timeout_seconds)
            return AgentExecutionResult(
                success=False,
                exit_code=-1,
                stdout=exc.stdout or "",
                stderr=f"TIMEOUT after {timeout_seconds}s\n{exc.stderr or ''}",
                elapsed_seconds=elapsed,
            )

        elapsed = time.monotonic() - handle.start_time
        log.info(
            f"{self.log_prefix}.finished",
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

    def terminate_session(self, handle: CliSessionHandle) -> None:
        # CLI tools are blocking calls; nothing to terminate
        pass

    def stream_logs(self, handle: CliSessionHandle) -> Iterator[str]:
        yield f"[{self.log_prefix}] no live log streaming — check stdout.txt after completion\n"