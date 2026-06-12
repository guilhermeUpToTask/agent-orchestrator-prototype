"""
tests/integration/test_system_start_lifecycle.py — child daemon lifecycle.

Verifies that the `system start` process-management helpers terminate all
child processes (no orphans), escalating to SIGKILL for stubborn children.
"""
from __future__ import annotations

import subprocess
import sys

from src.infra.cli.system.commands import _shutdown_processes


def _sleeper(seconds: int = 60) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({seconds})"])


def test_shutdown_terminates_all_children():
    procs = [("a", _sleeper()), ("b", _sleeper()), ("c", _sleeper())]
    _shutdown_processes(procs, timeout=5.0)
    assert all(p.poll() is not None for _name, p in procs)


def test_shutdown_kills_children_ignoring_sigterm():
    stubborn = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)",
        ]
    )
    import time

    time.sleep(0.3)  # let the child install its SIGTERM handler
    _shutdown_processes([("stubborn", stubborn)], timeout=1.0)
    assert stubborn.poll() is not None


def test_shutdown_tolerates_already_exited_children():
    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait()
    _shutdown_processes([("done", done)], timeout=1.0)
    assert done.poll() is not None


# ---------------------------------------------------------------------------
# Worker supervision (_supervise)
# ---------------------------------------------------------------------------


def test_supervise_restarts_crashed_worker_and_exits_on_api_death(monkeypatch):
    from unittest.mock import MagicMock

    from src.infra.cli.system import commands as sysc

    restarted = MagicMock()
    restarted.poll.return_value = None
    restarted.pid = 4242
    popen_calls: list = []

    def fake_popen(args, env=None):
        popen_calls.append(args)
        return restarted

    monkeypatch.setattr(sysc.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(sysc, "_BACKOFF_BASE_SECONDS", 0.01)
    monkeypatch.setattr(sysc.time, "sleep", lambda _s: None)

    # API stays up until the worker has been restarted, then "dies" so the
    # supervisor returns (an API exit ends the whole system).
    api = MagicMock()
    api.poll.side_effect = lambda: 1 if popen_calls else None
    api.returncode = 1

    dead_worker = MagicMock()
    dead_worker.poll.return_value = 1
    dead_worker.returncode = 1

    procs = [["api", api], ["w1", dead_worker]]
    worker_args = {"w1": ["system", "worker", "--agent-id", "w1"]}

    sysc._supervise(procs, worker_args, env={})

    assert len(popen_calls) == 1
    assert popen_calls[0][-4:] == ["system", "worker", "--agent-id", "w1"]
    # The supervisor swapped the dead Popen for the restarted one in place.
    assert procs[1][1] is restarted


def test_supervise_gives_up_after_max_consecutive_crashes(monkeypatch):
    from unittest.mock import MagicMock

    from src.infra.cli.system import commands as sysc

    popen_calls: list = []

    def fake_popen(args, env=None):
        crashing = MagicMock()
        crashing.poll.return_value = 1
        crashing.returncode = 1
        popen_calls.append(args)
        return crashing

    monkeypatch.setattr(sysc.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(sysc, "_BACKOFF_BASE_SECONDS", 0.001)
    monkeypatch.setattr(sysc, "_BACKOFF_CAP_SECONDS", 0.002)
    monkeypatch.setattr(sysc.time, "sleep", lambda _s: None)

    # End supervision once the worker has been abandoned: the API "dies"
    # after _MAX_CONSECUTIVE_CRASHES restarts have been attempted.
    api = MagicMock()
    api.poll.side_effect = (
        lambda: 1 if len(popen_calls) >= sysc._MAX_CONSECUTIVE_CRASHES else None
    )
    api.returncode = 1

    dead_worker = MagicMock()
    dead_worker.poll.return_value = 1
    dead_worker.returncode = 1

    procs = [["api", api], ["w1", dead_worker]]
    worker_args = {"w1": ["system", "worker", "--agent-id", "w1"]}

    sysc._supervise(procs, worker_args, env={})

    assert len(popen_calls) == sysc._MAX_CONSECUTIVE_CRASHES
