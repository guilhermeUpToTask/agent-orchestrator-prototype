"""`orchestrate serve` — the one command Phase 6 promises a new operator.

The deliverable is "start API, worker, and packaged frontend with one command
and an explicit state directory". Until now that took three: `db upgrade`,
`api start`, `worker start` — in separate shells, each needing the same
environment, and with no feedback if you forgot the third. A first-run operator
who skips the worker gets a plan that is accepted and then never moves, which
is the single hardest failure to diagnose from the outside because everything
reports healthy.

This spawns the real command against a temporary state directory and asks the
running system the only questions that matter: is the API answering, and did a
worker actually register? A worker that never started is exactly what
`GET /api/workers` exists to reveal.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _get(url: str, timeout: float = 2.0):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read())


def _wait_for(url: str, deadline_seconds: float = 45.0):
    deadline = time.monotonic() + deadline_seconds
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return _get(url)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:  # not up yet
            last = exc
            time.sleep(0.5)
    raise AssertionError(f"{url} never answered: {last}")


def _children_of(pid: int) -> list[int]:
    listing = subprocess.run(
        ["ps", "-o", "pid=", "--ppid", str(pid)],
        capture_output=True,
        text=True,
        check=False,
    )
    return [int(line) for line in listing.stdout.split() if line.strip()]


def _is_running(pid: int) -> bool:
    """Alive and not a zombie. A reaped-but-unwaited child still answers to
    signal 0, so `os.kill(pid, 0)` alone would call a corpse a leak."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


@pytest.fixture
def served(tmp_path):
    """The real `orchestrate serve`, on a free port, in its own process group."""
    port = _free_port()
    env = {
        **os.environ,
        "ORCHESTRATOR_HOME": str(tmp_path / "home"),
        "PYTHONPATH": str(BACKEND),
    }
    env.pop("ORCHESTRATOR_API_TOKEN", None)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "agent_orchestrator.infra.cli.main",
            "serve",
            "--port",
            str(port),
            "--poll-seconds",
            "0.2",
        ],
        cwd=str(BACKEND),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,  # own process group, so cleanup takes the worker too
    )
    try:
        yield process, port, tmp_path / "home"
    finally:
        if process.poll() is None:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:  # pragma: no cover - cleanup safety
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                process.wait(timeout=10)


def test_one_command_brings_up_the_api(served) -> None:
    _process, port, _home = served

    health = _wait_for(f"http://127.0.0.1:{port}/health")

    assert health["status"] == "ok"


def test_one_command_also_brings_up_a_worker(served) -> None:
    """The half an operator forgets. A plan with no worker is accepted and then
    never moves, and every other read still says healthy."""
    _process, port, _home = served
    _wait_for(f"http://127.0.0.1:{port}/health")

    deadline = time.monotonic() + 45
    workers: list = []
    while time.monotonic() < deadline:
        workers = _get(f"http://127.0.0.1:{port}/api/workers")
        if any(not worker["stale"] for worker in workers):
            break
        time.sleep(0.5)

    assert any(not worker["stale"] for worker in workers), f"no live worker: {workers}"


def test_it_migrates_the_state_directory_it_was_given(served) -> None:
    """An explicit state directory that starts empty: `serve` has to create the
    schema, or the first request against a real route fails on a missing table
    and the operator is told nothing useful."""
    _process, port, home = served
    _wait_for(f"http://127.0.0.1:{port}/health")

    assert (home / "orchestrator.db").is_file()
    assert _get(f"http://127.0.0.1:{port}/api/plans") == []


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
def test_sigterm_to_serve_takes_the_worker_with_it(served) -> None:
    """SIGTERM to `serve` alone — the shape every process manager uses, and the
    one no other test covers because they all tear down by process GROUP.

    uvicorn captures SIGTERM, drains, restores the previous handler and then
    re-raises the signal, so the process dies *inside* `uvicorn.run()` and the
    `finally` that reaps the worker never executes. The API stops, the worker
    lives on against the same state directory: it keeps claiming plans, keeps
    spending provider tokens, and the next `serve` puts a second worker beside
    it. Nothing on the control plane can report it, because the control plane
    is the half that exited.
    """
    process, port, _home = served
    _wait_for(f"http://127.0.0.1:{port}/health")
    workers = _children_of(process.pid)
    assert workers, "serve started no worker subprocess to begin with"

    process.terminate()  # the supervisor only — NOT os.killpg
    process.wait(timeout=45)

    deadline = time.monotonic() + 45
    while time.monotonic() < deadline and any(_is_running(pid) for pid in workers):
        time.sleep(0.5)
    leaked = [pid for pid in workers if _is_running(pid)]
    assert not leaked, f"worker survived its supervisor: {leaked}"
