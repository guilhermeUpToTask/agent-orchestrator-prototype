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
