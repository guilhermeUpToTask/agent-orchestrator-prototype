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

import os
import select
import subprocess
import time
from typing import Callable, Iterator, Optional

import structlog

from src.domain import AgentExecutionResult, AgentProps, ExecutionContext
from src.domain import AgentRuntimePort, SessionHandle

from .live_logger import get_logger
from .log_events import (
    build_agent_end_event,
    build_agent_start_event,
    build_stderr_event,
    build_stdout_event,
)

log = structlog.get_logger(__name__)


def _as_text(data: "bytes | str | None") -> str:
    """TimeoutExpired captures bytes or str depending on text mode."""
    if data is None:
        return ""
    return data.decode(errors="replace") if isinstance(data, bytes) else data


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
        progress_cb: Optional[Callable[[str], None]] = None,
    ) -> AgentExecutionResult:
        """
        Wait for agent completion with real-time stdout/stderr streaming.

        This method intercepts subprocess execution and streams output
        line-by-line as it arrives, rather than buffering all at once.
        progress_cb, if given, is invoked with each output line so callers can
        surface live progress (e.g. publish task.progress events).
        """
        if not isinstance(handle, LoggingSessionHandle):
            raise TypeError(f"Expected LoggingSessionHandle, got {type(handle)}")

        # Check if base runtime can stream logs natively
        # (Some runtimes might have their own streaming capability)
        try:
            # Try to use native streaming if available
            for line in self._base_runtime.stream_logs(handle.real_handle):
                self._logger.log_event(build_stdout_event(self._agent_name, line))
                if progress_cb is not None:
                    progress_cb(line)
        except Exception:
            # Fall back to subprocess-based streaming
            pass

        # For CLI runtimes, we need to intercept subprocess execution
        # We'll call the base runtime and capture output with streaming
        result = self._capture_streamed_execution(
            handle,
            timeout_seconds,
            progress_cb,
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
        progress_cb: Optional[Callable[[str], None]] = None,
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
                    progress_cb,
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
                if progress_cb is not None:
                    progress_cb(line)

        if result.stderr:
            for line in result.stderr.splitlines():
                self._logger.log_event(build_stderr_event(self._agent_name, line))
                if progress_cb is not None:
                    progress_cb(line)

        return result

    def _execute_with_streaming(
        self,
        cmd: list[str],
        cwd: str,
        env: dict[str, str],
        timeout_seconds: int,
        progress_cb: Optional[Callable[[str], None]] = None,
    ) -> AgentExecutionResult:
        """Execute the agent and stream its output line-by-line.

        Prefers a PTY so the child sees a terminal and line-flushes (CLIs
        block-buffer when stdout is a pipe, which suppresses live output). Falls
        back to the pipe implementation when no PTY can be allocated (PTY
        exhaustion or non-Unix).
        """
        try:
            return self._execute_via_pty(cmd, cwd, env, timeout_seconds, progress_cb)
        except (OSError, NotImplementedError) as exc:
            log.warning(
                "runtime_wrapper.pty_unavailable_falling_back",
                agent=self._agent_name,
                error=str(exc),
            )
            return self._execute_via_pipe(cmd, cwd, env, timeout_seconds, progress_cb)

    def _execute_via_pty(
        self,
        cmd: list[str],
        cwd: str,
        env: dict[str, str],
        timeout_seconds: int,
        progress_cb: Optional[Callable[[str], None]] = None,
    ) -> AgentExecutionResult:
        """Run *cmd* under pseudo-terminals so the child line-flushes its output.

        Two PTYs keep stdout/stderr separate (preserving AgentExecutionResult
        semantics). Output is read in chunks and split into lines, each emitted
        to the LiveLogger and progress_cb as it arrives.
        """
        import pty

        start_time = time.monotonic()
        m_out = s_out = m_err = s_err = -1
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        buffers: dict[int, str] = {}

        def _emit(fd: int, chunk: str, *, final: bool = False) -> None:
            # Per-fd partial-line buffer; emit only on newline, flush at EOF.
            buffers[fd] += chunk
            lines = buffers[fd].split("\n")
            buffers[fd] = "" if final else lines.pop()
            for raw in lines:
                line = raw.rstrip("\r")
                if not line and final:
                    continue
                if fd == m_out:
                    stdout_lines.append(line)
                    self._logger.log_event(build_stdout_event(self._agent_name, line))
                else:
                    stderr_lines.append(line)
                    self._logger.log_event(build_stderr_event(self._agent_name, line))
                if progress_cb is not None:
                    progress_cb(line)

        try:
            # openpty raises OSError on exhaustion / NotImplementedError off-Unix;
            # either propagates to the caller's fallback after the finally cleans up.
            m_out, s_out = pty.openpty()
            m_err, s_err = pty.openpty()
            buffers = {m_out: "", m_err: ""}

            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                env=env,
                stdout=s_out,
                stderr=s_err,
                stdin=subprocess.DEVNULL,
                close_fds=True,
            )
            # Close the slave ends in the parent so EOF is seen when the child exits.
            os.close(s_out)
            s_out = -1
            os.close(s_err)
            s_err = -1

            open_fds = [m_out, m_err]
            while open_fds:
                ready, _, _ = select.select(open_fds, [], [], 0.1)
                for fd in ready:
                    try:
                        data = os.read(fd, 4096)
                    except OSError:
                        data = b""  # EIO on Linux when the slave closes → EOF
                    if data:
                        _emit(fd, data.decode(errors="replace"))
                    else:
                        _emit(fd, "", final=True)
                        open_fds.remove(fd)

                if proc.poll() is not None and not ready:
                    break  # process gone and nothing buffered to drain

                if time.monotonic() - start_time > timeout_seconds:
                    proc.kill()
                    proc.wait()
                    raise subprocess.TimeoutExpired(cmd, timeout_seconds)

            # Flush any trailing partial line left if the loop broke early.
            for fd in list(buffers):
                if buffers[fd]:
                    _emit(fd, "", final=True)

            exit_code = proc.wait(timeout=5)
            return AgentExecutionResult(
                success=exit_code == 0,
                exit_code=exit_code,
                stdout="\n".join(stdout_lines),
                stderr="\n".join(stderr_lines),
                elapsed_seconds=time.monotonic() - start_time,
            )
        except subprocess.TimeoutExpired:
            return AgentExecutionResult(
                success=False,
                exit_code=-1,
                stdout="\n".join(stdout_lines),
                stderr=f"TIMEOUT after {timeout_seconds}s\n" + "\n".join(stderr_lines),
                elapsed_seconds=time.monotonic() - start_time,
            )
        finally:
            for fd in (m_out, m_err, s_out, s_err):
                if fd != -1:
                    try:
                        os.close(fd)
                    except OSError:
                        pass

    def _execute_via_pipe(
        self,
        cmd: list[str],
        cwd: str,
        env: dict[str, str],
        timeout_seconds: int,
        progress_cb: Optional[Callable[[str], None]] = None,
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
            assert proc.stdout is not None and proc.stderr is not None  # PIPE above
            stdout_lines = []
            stderr_lines = []

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
                            if progress_cb is not None:
                                progress_cb(line)
                    if remaining_stderr:
                        for line in remaining_stderr.splitlines():
                            stderr_lines.append(line)
                            self._logger.log_event(build_stderr_event(self._agent_name, line))
                            if progress_cb is not None:
                                progress_cb(line)
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
                        if progress_cb is not None:
                            progress_cb(line.rstrip())

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
                stdout=_as_text(exc.stdout),
                stderr=f"TIMEOUT after {timeout_seconds}s\n{_as_text(exc.stderr)}",
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
