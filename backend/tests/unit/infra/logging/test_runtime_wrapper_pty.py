"""
tests/unit/infra/logging/test_runtime_wrapper_pty.py

PTY-backed live streaming: the agent child sees a terminal (so it line-flushes)
and output is emitted line-by-line; a pipe fallback covers PTY-less environments.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock


from src.infra.logging.runtime_wrapper import LoggingRuntimeWrapper


def _wrapper() -> LoggingRuntimeWrapper:
    w = LoggingRuntimeWrapper(base_runtime=MagicMock(), agent_name="test")
    w._logger = MagicMock()  # silence terminal output; not under test here
    return w


def _run_pty(cmd: list[str]):
    captured: list[str] = []
    result = _wrapper()._execute_via_pty(
        cmd, cwd=".", env={"PATH": "/usr/bin:/bin"}, timeout_seconds=30,
        progress_cb=captured.append,
    )
    return result, captured


def test_child_sees_a_tty_under_pty():
    result, captured = _run_pty(
        [sys.executable, "-c", "import sys; print('tty' if sys.stdout.isatty() else 'notty')"]
    )
    assert result.exit_code == 0
    assert any("tty" in line and "notty" not in line for line in captured)


def test_lines_stream_individually():
    result, captured = _run_pty(
        [sys.executable, "-c", "import sys\nfor i in range(3): print(f'line{i}', flush=True)"]
    )
    assert result.success
    joined = "\n".join(captured)
    assert "line0" in joined and "line1" in joined and "line2" in joined


def test_falls_back_to_pipe_when_pty_unavailable(monkeypatch):
    import pty

    monkeypatch.setattr(pty, "openpty", lambda: (_ for _ in ()).throw(OSError("no pty")))
    captured: list[str] = []
    result = _wrapper()._execute_with_streaming(
        [sys.executable, "-c", "print('from pipe')"],
        cwd=".", env={"PATH": "/usr/bin:/bin"}, timeout_seconds=30,
        progress_cb=captured.append,
    )
    assert result.exit_code == 0
    assert any("from pipe" in line for line in captured)


def test_timeout_kills_and_reports():
    result = _wrapper()._execute_via_pty(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=".", env={"PATH": "/usr/bin:/bin"}, timeout_seconds=1,
        progress_cb=lambda _line: None,
    )
    assert result.success is False
    assert result.exit_code == -1
    assert "TIMEOUT" in result.stderr
