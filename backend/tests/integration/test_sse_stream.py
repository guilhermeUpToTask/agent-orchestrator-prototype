"""tests/integration/test_sse_stream.py — the SSE stream actually delivers.

httpx's built-in ASGITransport (and starlette's TestClient, which also relies
on it) buffers a whole ASGI response before returning it to the caller — it
awaits `app(scope, receive, send)` to completion, so it cannot represent a
long-lived stream that never "completes" (see
httpx/_transports/asgi.py::ASGITransport.handle_async_request). `/api/events`
is exactly that: its generator only returns on client disconnect. So neither
the sync `TestClient` (deadlocks if the SAME client also drives a mutation —
one worker thread for both) nor an in-process ASGITransport client can
exercise a real stream.

This test instead runs the real app under a real `uvicorn.Server` on a
background thread bound to an ephemeral localhost port, and talks to it over
an actual TCP socket with `httpx` — the same wire path the frontend and
outbox relay design assume, and the only way to observe incremental framing
at all."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone

import httpx
import pytest
import uvicorn
from cryptography.fernet import Fernet

from src.api import dependencies
from src.api.server import create_app
from src.app.execution_records import (
    ExecutionAttempt,
    ExecutionAttemptStatus,
    ExecutionRun,
    ExecutionRunStatus,
)
from src.domain.entities.project_definition import ProjectDefinition
from src.infra.container import AppContainer
from src.infra.db.tables import Base
from src.infra.runtime.process_supervisor import attempt_log_path

pytestmark = pytest.mark.integration

_TIMEOUT = 10.0


@pytest.fixture
def live_server(tmp_path, monkeypatch):
    """A real API process (uvicorn, in-thread) with the outbox relay running,
    reachable over an actual loopback socket."""
    monkeypatch.setenv("ORCHESTRATOR_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("ORCHESTRATOR_API_TOKEN", raising=False)
    container = AppContainer(orchestrator_home=tmp_path)
    Base.metadata.create_all(container.engine)
    container.project_repo.add(ProjectDefinition(id="project-1", name="Test project", repo_url=None))
    app = create_app(container)

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + _TIMEOUT
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started, "uvicorn server failed to start within timeout"
    port = server.servers[0].sockets[0].getsockname()[1]

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=_TIMEOUT)
        assert not thread.is_alive(), "uvicorn server thread did not shut down cleanly"
        dependencies.set_container(None)  # type: ignore[arg-type]


def test_sse_stream_delivers_named_event_with_event_id(live_server):
    """A real streaming client connected to /api/events receives a NAMED SSE
    frame (`event: <type>`) whose JSON payload carries `event_id`, produced by
    an ordinary HTTP mutation (plan create + discovery message — the same
    flow test_outbox_relay.py::test_relay_end_to_end_through_http_mutation
    uses to trigger `IntentProposed`) and delivered through the real relay
    thread and the real broker. The reader closes the stream itself on
    receipt, proving the connection doesn't hang open."""
    base_url = live_server
    frames: list[tuple[str, dict]] = []
    connected = threading.Event()
    done = threading.Event()
    errors: list[BaseException] = []

    def consume() -> None:
        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                with client.stream("GET", f"{base_url}/api/events") as response:
                    assert response.status_code == 200
                    connected.set()
                    event_type: str | None = None
                    for line in response.iter_lines():
                        if line.startswith("event: "):
                            event_type = line[len("event: ") :].strip()
                        elif line.startswith("data: "):
                            payload = json.loads(line[len("data: ") :])
                            frames.append((event_type or "", payload))
                            if event_type == "IntentProposed":
                                return  # exits `with` -> closes the connection
        except BaseException as exc:  # surfaced on the main thread, not swallowed
            errors.append(exc)
        finally:
            connected.set()
            done.set()

    reader = threading.Thread(target=consume, daemon=True)
    reader.start()
    assert connected.wait(_TIMEOUT), "SSE client never connected"
    assert not errors, f"stream reader failed before the mutation: {errors}"

    with httpx.Client(base_url=base_url, timeout=_TIMEOUT) as client:
        created = client.post(
            "/api/plans",
            json={"brief": "goal: G\ntask: t", "project_id": "project-1"},
        )
        assert created.status_code == 201
        plan_id = created.json()["plan_id"]
        client.post(f"/api/plans/{plan_id}/discovery/message", json={"message": ""})

    assert done.wait(_TIMEOUT), "SSE client never received IntentProposed"
    reader.join(timeout=_TIMEOUT)
    assert not reader.is_alive(), "stream reader thread hung instead of closing cleanly"
    assert not errors, f"stream reader raised: {errors}"

    assert frames, "no SSE frames received on the stream"
    event_types = [event_type for event_type, _ in frames]
    assert "IntentProposed" in event_types

    intent_payload = next(payload for event_type, payload in frames if event_type == "IntentProposed")
    assert intent_payload["plan_id"] == plan_id
    assert intent_payload.get("event_id")
    assert json.dumps(intent_payload)  # payload is JSON-serializable


@pytest.fixture
def live_stack(tmp_path, monkeypatch):
    """Same real-uvicorn stack as `live_server`, but also hands back the
    container so a test can seed execution rows and write runtime-log files the
    API will then read/stream."""
    monkeypatch.setenv("ORCHESTRATOR_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("ORCHESTRATOR_API_TOKEN", raising=False)
    container = AppContainer(orchestrator_home=tmp_path)
    Base.metadata.create_all(container.engine)
    container.project_repo.add(ProjectDefinition(id="project-1", name="Test project", repo_url=None))
    app = create_app(container)

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + _TIMEOUT
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started, "uvicorn server failed to start within timeout"
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}", container
    finally:
        server.should_exit = True
        thread.join(timeout=_TIMEOUT)
        assert not thread.is_alive(), "uvicorn server thread did not shut down cleanly"
        dependencies.set_container(None)  # type: ignore[arg-type]


def _seed_terminal_attempt(container: AppContainer, plan_id: str) -> str:
    """Insert a SUCCEEDED run+attempt for an existing plan and return the
    attempt id (FK: execution rows reference plans.id)."""
    now = datetime.now(timezone.utc)
    run_id, attempt_id = "run-1", "attempt-1"
    with container.new_unit_of_work() as uow:
        uow.executions.add_run(
            ExecutionRun(
                id=run_id,
                plan_id=plan_id,
                goal_id="goal-1",
                task_id="task-1",
                status=ExecutionRunStatus.SUCCEEDED,
                started_at=now,
                completed_at=now,
            )
        )
        uow.executions.add_attempt(
            ExecutionAttempt(
                id=attempt_id,
                run_id=run_id,
                plan_id=plan_id,
                goal_id="goal-1",
                task_id="task-1",
                number=1,
                task_attempt=1,
                status=ExecutionAttemptStatus.SUCCEEDED,
                started_at=now,
                completed_at=now,
            )
        )
    return attempt_id


def test_attempt_log_stream_tails_raw_runtime_output_then_ends(live_stack):
    """The live runtime-log stream endpoint delivers the RAW per-attempt
    stdout/stderr lines as SSE `id:`/`data:` frames — separate from the
    telemetry `/api/events` feed — and closes with `event: end` once the
    attempt is terminal and the log is fully drained."""
    base_url, container = live_stack
    with httpx.Client(base_url=base_url, timeout=_TIMEOUT) as client:
        created = client.post(
            "/api/plans", json={"brief": "goal: G\ntask: t", "project_id": "project-1"}
        )
        assert created.status_code == 201
        plan_id = created.json()["plan_id"]

    attempt_id = _seed_terminal_attempt(container, plan_id)

    # Write the raw runtime log exactly as _BoundedLog does: one JSON record/line.
    written = [
        {"monotonic_seconds": 1.0, "stream": "stdout", "text": "cloning repo\n"},
        {"monotonic_seconds": 1.4, "stream": "stderr", "text": "warning: detached HEAD\n"},
        {"monotonic_seconds": 2.1, "stream": "stdout", "text": "tests passed\n"},
    ]
    log_path = attempt_log_path(container.orchestrator_home, attempt_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "".join(json.dumps(record) + "\n" for record in written), encoding="utf-8"
    )

    records: list[dict] = []
    saw_end = False
    with httpx.Client(timeout=_TIMEOUT) as client:
        url = f"{base_url}/api/plans/{plan_id}/attempts/{attempt_id}/log/stream"
        with client.stream("GET", url) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            current_event: str | None = None
            for line in response.iter_lines():
                if line.startswith("event: "):
                    current_event = line[len("event: ") :].strip()
                elif line.startswith("data: "):
                    payload = line[len("data: ") :]
                    if current_event == "end":
                        saw_end = True
                        break
                    records.append(json.loads(payload))
                elif line == "":
                    current_event = None

    assert saw_end, "stream never emitted event: end for a terminal attempt"
    assert [r["text"] for r in records] == [w["text"] for w in written]
    assert [r["stream"] for r in records] == ["stdout", "stderr", "stdout"]


def test_attempt_log_stream_unknown_attempt_is_404(live_stack):
    base_url, _ = live_stack
    with httpx.Client(base_url=base_url, timeout=_TIMEOUT) as client:
        created = client.post(
            "/api/plans", json={"brief": "goal: G\ntask: t", "project_id": "project-1"}
        )
        plan_id = created.json()["plan_id"]
        resp = client.get(f"/api/plans/{plan_id}/attempts/does-not-exist/log/stream")
    assert resp.status_code == 404
