"""`praxis serve` — the one command Phase 6 promises a new operator.

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


# Generous on purpose. The server has to import the app, migrate a database
# with `synchronous=FULL` (every commit fsyncs) and bind a port — and under a
# parallel suite it does that while seven other workers hammer the same disk.
# 45s was calibrated for a serial run and turned a slow start into a failure;
# `_wait_for` returns the instant the server answers, so headroom is free, and
# the liveness check below still fails immediately when it is genuinely dead.
_STARTUP_DEADLINE = 180.0


def _wait_for(url: str, deadline_seconds: float = _STARTUP_DEADLINE, process=None, log: Path | None = None):
    """Poll `url` until it answers — but stop the moment the server is DEAD.

    Without the liveness check this waited the full deadline on a process that
    had already exited and reported "never answered: Connection refused":
    true, useless, and indistinguishable from a slow boot. `log` is the child's
    own output, which says in one line what the polling never could.
    """
    deadline = time.monotonic() + deadline_seconds
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return _get(url)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:  # not up yet
            last = exc
            if process is not None and process.poll() is not None:
                raise AssertionError(
                    f"server exited with {process.returncode} before answering {url}:"
                    f"\n{_tail(log)}"
                ) from exc
            time.sleep(0.5)
    raise AssertionError(f"{url} never answered ({last}); server said:\n{_tail(log)}")


def _tail(log: Path | None, lines: int = 20) -> str:
    if log is None or not log.is_file():
        return "<no server log>"
    return "\n".join(log.read_text(errors="replace").splitlines()[-lines:])


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


def _spawn(port: int, home: Path, log: Path) -> subprocess.Popen:
    env = {
        **os.environ,
        "PRAXIS_HOME": str(home),
        "PYTHONPATH": str(BACKEND),
        # Otherwise the child block-buffers to the log file and a failure
        # reports an EMPTY server log, which is the one thing it must not do.
        "PYTHONUNBUFFERED": "1",
    }
    env.pop("PRAXIS_API_TOKEN", None)
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "praxis_orchestrator.infra.cli.main",
            "serve",
            "--port",
            str(port),
            "--poll-seconds",
            "0.2",
        ],
        cwd=str(BACKEND),
        env=env,
        # A FILE, not a pipe. `serve` and its worker log structurally on every
        # tick, and with `--poll-seconds 0.2` that fills a 64K pipe nobody is
        # draining — at which point the child BLOCKS on write and the API stops
        # answering. Serially the tests finished long before that; under a
        # parallel suite they do not, which is what made these tests flaky.
        stdout=log.open("wb"),
        stderr=subprocess.STDOUT,
        start_new_session=True,  # own process group, so cleanup takes the worker too
    )


@pytest.fixture
def served(tmp_path):
    """The real `praxis serve`, on a free port, in its own process group.

    `_free_port` binds a port, closes it, and hands the number over — so
    between that and uvicorn's own bind there is a window another process can
    take it. Serially that never happened; running the suite in parallel it
    does, uvicorn exits "address already in use", and the test then polls a
    corpse for 45 seconds. Retry the whole spawn rather than widen a deadline:
    losing a port race is not a slow start, and waiting longer never fixes it.
    """
    home = tmp_path / "home"
    log = tmp_path / "serve.log"
    for attempt in range(3):
        port = _free_port()
        process = _spawn(port, home, log)
        time.sleep(0.5)
        if process.poll() is None:
            break
        if attempt == 2:  # pragma: no cover - three lost races in a row
            raise AssertionError(f"serve could not start after 3 ports:\n{_tail(log)}")
    try:
        yield process, port, home, log
    finally:
        if process.poll() is None:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:  # pragma: no cover - cleanup safety
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                process.wait(timeout=10)


def test_one_command_brings_up_the_api(served) -> None:
    process, port, _home, log = served

    health = _wait_for(f"http://127.0.0.1:{port}/health", process=process, log=log)

    assert health["status"] == "ok"


def test_one_command_also_brings_up_a_worker(served) -> None:
    """The half an operator forgets. A plan with no worker is accepted and then
    never moves, and every other read still says healthy."""
    process, port, _home, log = served
    _wait_for(f"http://127.0.0.1:{port}/health", process=process, log=log)

    deadline = time.monotonic() + _STARTUP_DEADLINE
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
    process, port, home, log = served
    _wait_for(f"http://127.0.0.1:{port}/health", process=process, log=log)

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
    process, port, _home, log = served
    _wait_for(f"http://127.0.0.1:{port}/health", process=process, log=log)
    workers = _children_of(process.pid)
    assert workers, "serve started no worker subprocess to begin with"

    process.terminate()  # the supervisor only — NOT os.killpg
    process.wait(timeout=45)

    deadline = time.monotonic() + 45
    while time.monotonic() < deadline and any(_is_running(pid) for pid in workers):
        time.sleep(0.5)
    leaked = [pid for pid in workers if _is_running(pid)]
    assert not leaked, f"worker survived its supervisor: {leaked}"
