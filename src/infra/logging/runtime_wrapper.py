"""
src/infra/logging/runtime_wrapper.py — Non-intrusive wrapper for AgentRuntimePort.

Wraps any AgentRuntimePort implementation to add live logging capabilities
without modifying the original runtime code.

Key features:
  - Intercepts all runtime method calls
  - Streams stdout/stderr in real-time during execution
  - Emits structured events (AGENT_START, AGENT_END, etc.)
  - Thread-safe for concurrent agents
  - Zero changes to existing runtime implementations
"""
from __future__ import annotations

import select
import subprocess
import time
from typing import Iterator, Optional

from src.domain import AgentExecutionResult, AgentProps, ExecutionContext
from src.domain import AgentRuntimePort, SessionHandle

from .live_logger import get_logger
from .log_events import (
    build_agent_end_event,
    build_agent_start_event,
    build_stderr_event,
    build_stdout_event,
)


class LoggingSessionHandle(SessionHandle):
    """
    Wrapper around a real SessionHandle that adds logging context.
    """

    def __init__(
        self,
        real_handle: SessionHandle,
        agent_name: str,
        session_id: str,
    ) -> None:
        self._real_handle = real_handle
        self._agent_name = agent_name
        self._session_id = session_id
        self._start_time = time.monotonic()

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def real_handle(self) -> SessionHandle:
        return self._real_handle

    @property
    def agent_name(self) -> str:
        return self._agent_name

    @property
    def start_time(self) -> float:
        return self._start_time


class LoggingRuntimeWrapper(AgentRuntimePort):
    """
    Non-intrusive wrapper that adds live logging to any AgentRuntimePort.

    Usage:
        runtime = LoggingRuntimeWrapper(
            base_runtime=ClaudeCodeRuntime(...),
            agent_name="claude",
        )

    The wrapper intercepts:
      - start_session: Emits AGENT_START event
      - wait_for_completion: Streams stdout/stderr in real-time
      - terminate_session: Properly closes logging
    """

    def __init__(
        self,
        base_runtime: AgentRuntimePort,
        agent_name: str,
        json_log_dir: Optional[str] = None,
    ) -> None:
        """
        Initialize the logging wrapper.

        Args:
            base_runtime: The underlying runtime to wrap
            agent_name: Name for logging (e.g., "pi", "gemini", "claude")
            json_log_dir: Directory to store JSON logs (optional)
        """
        self._base_runtime = base_runtime
        self._agent_name = agent_name
        self._logger = get_logger()

    def start_session(
        self,
        agent_props: AgentProps,
        workspace_path: str,
        env_vars: dict[str, str],
    ) -> SessionHandle:
        """
        Start a session and emit AGENT_START event.
        """
        # Start the base runtime session
        real_handle = self._base_runtime.start_session(agent_props, workspace_path, env_vars)

        # Create logging session handle
        session_id = real_handle.session_id
        logging_handle = LoggingSessionHandle(real_handle, self._agent_name, session_id)

        # Register agent with logger
        self._logger.register_agent(
            agent_name=self._agent_name,
            session_id=session_id,
            workspace_path=workspace_path,
            metadata={
                "agent_id": agent_props.agent_id,
                "runtime_type": agent_props.runtime_type,
            },
        )

        # Emit AGENT_START event
        start_event = build_agent_start_event(
            agent_name=self._agent_name,
            session_id=session_id,
            workspace=workspace_path,
        )
        self._logger.log_event(start_event)

        return logging_handle

    def send_execution_payload(
        self,
        handle: SessionHandle,
        context: ExecutionContext,
    ) -> None:
        """
        Forward to base runtime.
        """
        if not isinstance(handle, LoggingSessionHandle):
            raise TypeError(f"Expected LoggingSessionHandle, got {type(handle)}")

        # Optionally emit LLM_REQUEST event if we can determine model info
        # (This would require reading from the context or agent props)
        self._base_runtime.send_execution_payload(handle.real_handle, context)

    def wait_for_completion(
        self,
        handle: SessionHandle,
        timeout_seconds: int = 600,
    ) -> AgentExecutionResult:
        """
        Wait for agent completion with real-time stdout/stderr streaming.

        This method intercepts subprocess execution and streams output
        line-by-line as it arrives, rather than buffering all at once.
        """
        if not isinstance(handle, LoggingSessionHandle):
            raise TypeError(f"Expected LoggingSessionHandle, got {type(handle)}")

        # Check if base runtime can stream logs natively
        # (Some runtimes might have their own streaming capability)
        try:
            # Try to use native streaming if available
            for line in self._base_runtime.stream_logs(handle.real_handle):
                self._logger.log_event(build_stdout_event(self._agent_name, line))
        except Exception:
            # Fall back to subprocess-based streaming
            pass

        # For CLI runtimes, we need to intercept subprocess execution
        # We'll call the base runtime and capture output with streaming
        result = self._capture_streamed_execution(
            handle,
            timeout_seconds,
        )

        # Emit final events
        elapsed = time.monotonic() - handle.start_time

        if result.stdout:
            self._logger.log_event(build_stdout_event(
                self._agent_name,
                f"[captured] {len(result.stdout)} bytes of stdout\n"
            ))

        if result.stderr:
            self._logger.log_event(build_stderr_event(
                self._agent_name,
                f"[captured] {len(result.stderr)} bytes of stderr\n"
            ))

        # AGENT_END event
        end_event = build_agent_end_event(
            agent_name=self._agent_name,
            session_id=handle.session_id,
            exit_code=result.exit_code,
            elapsed=elapsed,
        )
        self._logger.log_event(end_event)

        return result

    def _capture_streamed_execution(
        self,
        handle: LoggingSessionHandle,
        timeout_seconds: int,
    ) -> AgentExecutionResult:
        """
        Execute the agent with real-time output streaming.

        This method intercepts subprocess execution to stream output
        line-by-line to the logger.
        """
        # Get the command and environment from the base runtime
        # by inspecting the base runtime's internal state
        # (This is a bit hacky, but necessary for non-intrusive wrapping)
        try:
            # Try to build command using the base runtime's methods
            # This works for CliAgentRuntime subclasses
            from src.infra.runtime.agent_runtime import CliAgentRuntime, CliSessionHandle

            if isinstance(self._base_runtime, CliAgentRuntime) and isinstance(handle.real_handle, CliSessionHandle):
                cmd = self._base_runtime._build_cmd(handle.real_handle)
                env = self._base_runtime._get_env()
                workspace = handle.real_handle.workspace_path

                # Execute with streaming
                return self._execute_with_streaming(
                    cmd,
                    workspace,
                    env,
                    timeout_seconds,
                )
        except Exception as e:
            # If we can't intercept, fall back to base runtime
            # This preserves functionality even if wrapping fails
            import structlog
            log = structlog.get_logger(__name__)
            log.warning("Could not intercept subprocess, falling back", error=str(e))

        # Fall back to base runtime's blocking execution
        # Capture output and stream it line-by-line
        result = self._base_runtime.wait_for_completion(handle.real_handle, timeout_seconds)

        # Stream captured output to logger
        if result.stdout:
            for line in result.stdout.splitlines():
                self._logger.log_event(build_stdout_event(self._agent_name, line))

        if result.stderr:
            for line in result.stderr.splitlines():
                self._logger.log_event(build_stderr_event(self._agent_name, line))

        return result

    def _execute_with_streaming(
        self,
        cmd: list[str],
        cwd: str,
        env: dict[str, str],
        timeout_seconds: int,
    ) -> AgentExecutionResult:
        """
        Execute a subprocess with real-time output streaming.

        This method uses Popen to read stdout/stderr line-by-line
        as they become available, streaming to the logger.
        """
        start_time = time.monotonic()

        try:
            # Use Popen for streaming output
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # Line-buffered
            )

            # Read output streams in real-time using select
            stdout_lines = []
            stderr_lines = []
            timeout_remaining = timeout_seconds

            while True:
                # Check if process has finished
                if proc.poll() is not None:
                    # Read any remaining output
                    remaining_stdout = proc.stdout.read()
                    remaining_stderr = proc.stderr.read()
                    if remaining_stdout:
                        for line in remaining_stdout.splitlines():
                            stdout_lines.append(line)
                            self._logger.log_event(build_stdout_event(self._agent_name, line))
                    if remaining_stderr:
                        for line in remaining_stderr.splitlines():
                            stderr_lines.append(line)
                            self._logger.log_event(build_stderr_event(self._agent_name, line))
                    break

                # Use select to check which streams have data
                ready, _, _ = select.select(
                    [proc.stdout, proc.stderr],
                    [],
                    [],
                    0.1,  # 100ms timeout for non-blocking check
                )

                for stream in ready:
                    line = stream.readline()
                    if line:
                        if stream == proc.stdout:
                            stdout_lines.append(line.rstrip())
                            self._logger.log_event(build_stdout_event(self._agent_name, line))
                        else:
                            stderr_lines.append(line.rstrip())
                            self._logger.log_event(build_stderr_event(self._agent_name, line))

                # Update timeout
                elapsed = time.monotonic() - start_time
                if elapsed > timeout_seconds:
                    proc.kill()
                    proc.wait()
                    raise subprocess.TimeoutExpired(cmd, timeout_seconds)

            # Wait for process to complete
            proc.wait(timeout=1)
            exit_code = proc.returncode

            elapsed = time.monotonic() - start_time

            return AgentExecutionResult(
                success=exit_code == 0,
                exit_code=exit_code,
                stdout="\n".join(stdout_lines),
                stderr="\n".join(stderr_lines),
                elapsed_seconds=elapsed,
            )

        except subprocess.TimeoutExpired as exc:
            elapsed = time.monotonic() - start_time
            return AgentExecutionResult(
                success=False,
                exit_code=-1,
                stdout=exc.stdout or "",
                stderr=f"TIMEOUT after {timeout_seconds}s\n{exc.stderr or ''}",
                elapsed_seconds=elapsed,
            )
        except Exception as exc:
            elapsed = time.monotonic() - start_time
            return AgentExecutionResult(
                success=False,
                exit_code=1,
                stdout="",
                stderr=f"Execution error: {exc}",
                elapsed_seconds=elapsed,
            )

    def terminate_session(self, handle: SessionHandle) -> None:
        """
        Terminate session and close logging.
        """
        if not isinstance(handle, LoggingSessionHandle):
            raise TypeError(f"Expected LoggingSessionHandle, got {type(handle)}")

        # Terminate base session
        self._base_runtime.terminate_session(handle.real_handle)

        # Close logger for this agent
        self._logger.close_agent(self._agent_name)

    def stream_logs(self, handle: SessionHandle) -> Iterator[str]:
        """
        Stream logs from the base runtime with logging wrapper.

        Returns an iterator of log lines.
        """
        if not isinstance(handle, LoggingSessionHandle):
            raise TypeError(f"Expected LoggingSessionHandle, got {type(handle)}")

        # Delegate to base runtime
        for line in self._base_runtime.stream_logs(handle.real_handle):
            yield line
