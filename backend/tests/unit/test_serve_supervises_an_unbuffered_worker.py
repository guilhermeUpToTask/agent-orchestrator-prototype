"""`serve` must spawn its worker unbuffered — P8.6 Task 4.1.

The worker is a separate process whose stdout `serve` inherits as a PIPE, not a
tty. Python block-buffers a pipe, so without `-u` the supervised worker's log
sits frozen at its startup banner for 4 KiB while attempts are actually
running: the operator sees a system indistinguishable from a wedged one. During
the P8.4 demo run this cost an hour of blind diagnosis before anyone suspected
buffering rather than the orchestrator.

This is invisible in every functional test — the worker works fine either way,
which is exactly why it survived — so the argv itself is what gets locked. The
test drives the real click command with the two things that would otherwise
take over the process (the child spawn and the uvicorn event loop) replaced,
and reads the argument vector the supervisor actually built.
"""

from __future__ import annotations

import subprocess
import sys

import pytest
from click.testing import CliRunner

from praxis_orchestrator.infra.cli.main import cli


class _NeverRunningChild:
    """A spawned process that is already gone, so teardown reaps nothing."""

    pid = 4321

    def poll(self) -> int:
        return 0

    def terminate(self) -> None:  # pragma: no cover - poll() short-circuits
        raise AssertionError("a child that already exited must not be signalled")


@pytest.fixture()
def spawned_argv(tmp_path, monkeypatch) -> list[str]:
    monkeypatch.setenv("PRAXIS_HOME", str(tmp_path / "state"))
    recorded: list[list[str]] = []

    def fake_popen(argv, *args, **kwargs):
        recorded.append(list(argv))
        return _NeverRunningChild()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    # uvicorn.run would block forever; the supervisor's `finally` still runs.
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: None)

    result = CliRunner().invoke(cli, ["serve", "--no-migrate", "--port", "0"])

    assert result.exit_code == 0, result.output
    assert len(recorded) == 1, f"expected exactly one worker spawn, got {recorded}"
    return recorded[0]


def test_the_supervised_worker_is_spawned_unbuffered(spawned_argv) -> None:
    assert "-u" in spawned_argv, (
        "serve spawned the worker without -u: its log will block-buffer into a "
        "pipe and a running worker will look frozen at its startup banner"
    )


def test_the_unbuffered_flag_reaches_the_interpreter_not_the_worker(
    spawned_argv,
) -> None:
    """`-u` is a python flag: after `-m` it becomes an argument to the worker
    command, which click rejects. Position is the whole fix."""
    assert spawned_argv[0] == sys.executable
    assert spawned_argv.index("-u") < spawned_argv.index("-m")


def test_serve_still_spawns_the_worker_it_is_supervising(spawned_argv) -> None:
    """Guards the fixture: an argv that no longer starts a worker would pass
    the flag assertions above for the wrong reason."""
    assert spawned_argv[spawned_argv.index("-m") + 1] == (
        "praxis_orchestrator.infra.cli.main"
    )
    assert "worker" in spawned_argv
    assert "start" in spawned_argv


def test_no_worker_spawns_nothing_to_buffer(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PRAXIS_HOME", str(tmp_path / "state"))
    recorded: list[list[str]] = []
    monkeypatch.setattr(
        subprocess, "Popen", lambda argv, *a, **k: recorded.append(list(argv))
    )
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: None)

    result = CliRunner().invoke(
        cli, ["serve", "--no-migrate", "--no-worker", "--port", "0"]
    )

    assert result.exit_code == 0, result.output
    assert recorded == []
