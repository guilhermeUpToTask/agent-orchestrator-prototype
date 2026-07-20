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

import httpx
import pytest
import uvicorn
from cryptography.fernet import Fernet

from src.api import dependencies
from src.api.server import create_app
from src.domain.entities.project_definition import ProjectDefinition
from src.infra.container import AppContainer
from src.infra.db.tables import Base

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
