"""The outbox relay: undelivered rows reach the SSE broker in order and are
marked delivered; re-runs don't re-deliver (forward progress); agent events are
tailed by cursor; at-least-once + event_id dedup key in every payload."""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import text

from src.api.outbox_relay import relay_once
from src.api.sse import SSEBroker
from src.app.testing.fakes import FakeClock
from src.domain.entities.project_definition import ProjectDefinition
from src.domain.events.outbox import PhaseAdvanced, TaskCompleted
from src.infra.db.engine import build_engine, make_session_factory
from src.infra.db.tables import Base
from src.infra.db.unit_of_work import SqliteUnitOfWork

pytestmark = pytest.mark.integration


class CollectingBroker(SSEBroker):
    def __init__(self):
        super().__init__()
        self.published: list[tuple[str, dict]] = []

    def publish(self, event_type: str, payload: dict) -> None:
        self.published.append((event_type, payload))


@pytest.fixture
def sf(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'relay.db'}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def test_relay_delivers_marks_and_makes_progress(sf):
    uow = SqliteUnitOfWork(sf, FakeClock())
    e1 = PhaseAdvanced(plan_id="p1", from_phase="running", to_phase="review")
    e2 = TaskCompleted(plan_id="p1", goal_id="g1", task_id="t1")
    with uow:
        uow.outbox.add(e1)
        uow.outbox.add(e2)

    broker = CollectingBroker()
    delivered, cursor = relay_once(sf, broker, agent_cursor=0)
    assert delivered == 2
    assert [t for t, _ in broker.published] == ["PhaseAdvanced", "TaskCompleted"]
    # every payload carries the consumer dedup key
    assert broker.published[0][1]["event_id"] == e1.event_id

    # second pass: nothing left (marked delivered)
    delivered, cursor = relay_once(sf, broker, agent_cursor=cursor)
    assert delivered == 0
    with sf() as s:
        undelivered = s.execute(
            text("SELECT COUNT(*) FROM outbox WHERE delivered_at IS NULL")
        ).scalar_one()
    assert undelivered == 0


def test_relay_tails_agent_events_by_cursor(sf):
    from src.domain.events.agent_events import AgentEvent
    from src.infra.db.agent_event_sink import SqliteAgentEventSink

    sink = SqliteAgentEventSink(sf)
    for seq in range(3):
        asyncio.run(
            sink.emit(
                AgentEvent(
                    plan_id="p1",
                    task_id="t1",
                    attempt=1,
                    seq=seq,
                    type="step",
                    payload={"i": str(seq)},
                )
            )
        )

    broker = CollectingBroker()
    _, cursor = relay_once(sf, broker, agent_cursor=0)
    agent_events = [p for t, p in broker.published if t == "agent.event"]
    assert [e["seq"] for e in agent_events] == [0, 1, 2]

    # cursor advanced: a second pass re-delivers nothing
    broker.published.clear()
    _, cursor = relay_once(sf, broker, agent_cursor=cursor)
    assert broker.published == []


def test_relay_forwards_plan_scoped_event_with_null_task_id(sf):
    from src.domain.events.agent_events import AgentEvent
    from src.infra.db.agent_event_sink import SqliteAgentEventSink

    sink = SqliteAgentEventSink(sf)
    asyncio.run(
        sink.emit(
            AgentEvent(
                plan_id="p1",
                task_id=None,  # plan-scoped reasoner telemetry
                attempt=0,
                seq=0,
                type="llm.call",
                payload={"total_tokens": "42"},
            )
        )
    )

    broker = CollectingBroker()
    relay_once(sf, broker, agent_cursor=0)
    (event,) = [p for t, p in broker.published if t == "agent.event"]
    assert event["task_id"] is None and event["type"] == "llm.call"
    assert event["payload"]["total_tokens"] == "42"


def test_relay_survives_crash_between_publish_and_mark(sf):
    """At-least-once: if marking fails after publishing, the next pass
    re-delivers the same event (same event_id — consumers dedup)."""
    uow = SqliteUnitOfWork(sf, FakeClock())
    event = PhaseAdvanced(plan_id="p1", from_phase="review", to_phase="done")
    with uow:
        uow.outbox.add(event)

    class CrashAfterPublish(CollectingBroker):
        def publish(self, event_type, payload):
            super().publish(event_type, payload)
            raise RuntimeError("crash between publish and mark")

    crashing = CrashAfterPublish()
    with pytest.raises(RuntimeError):
        relay_once(sf, crashing, agent_cursor=0)

    # row NOT marked -> re-delivered on the next (healthy) pass with same id
    healthy = CollectingBroker()
    delivered, _ = relay_once(sf, healthy, agent_cursor=0)
    assert delivered == 1
    assert healthy.published[0][1]["event_id"] == event.event_id
    assert crashing.published[0][1]["event_id"] == event.event_id


def test_relay_end_to_end_through_http_mutation(tmp_path, monkeypatch):
    """A mutation through the API writes outbox rows the relay thread delivers
    to a live SSE client payload (the full delivery pipeline)."""
    import time

    from fastapi.testclient import TestClient

    from src.api import dependencies
    from src.api.server import create_app
    from src.api.sse import get_broker
    from src.infra.container import AppContainer

    container = AppContainer(orchestrator_home=tmp_path)
    Base.metadata.create_all(container.engine)
    container.project_repo.add(
        ProjectDefinition(id="project-1", name="Test project", repo_url=None)
    )
    app = create_app(container)

    with TestClient(app) as client:
        received: list = []

        # observe the broker directly (a real SSE client would stream /api/events)
        original = get_broker().publish
        get_broker().publish = lambda t, p: received.append((t, p))  # type: ignore
        try:
            plan_id = client.post(
                "/api/plans",
                json={
                    "brief": "goal: G\ntask: t",
                    "project_id": "project-1",
                },
            ).json()["plan_id"]
            client.post(f"/api/plans/{plan_id}/discovery/message", json={"message": ""})
            deadline = time.time() + 5
            while time.time() < deadline:
                if any(t == "PhaseAdvanced" for t, _ in received):
                    break
                time.sleep(0.05)
        finally:
            get_broker().publish = original  # type: ignore
        dependencies.set_container(None)  # type: ignore[arg-type]

    phase_events = [p for t, p in received if t == "PhaseAdvanced"]
    assert phase_events and phase_events[0]["to_phase"] == "architecture"
    assert json.dumps(phase_events[0])  # payload is JSON-serializable
