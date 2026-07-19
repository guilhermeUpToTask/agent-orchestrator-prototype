"""
src/api/outbox_relay.py — delivers outbox rows to their consumers (roadmap 4.4).

Without this, events are written but never seen: the transactional outbox only
guarantees events exist atomically with state — something must ship them. The
simplest correct prototype: a poller thread in the API process that reads
undelivered rows in id order, pushes them to the SSE broker, and marks them
delivered. Publish-then-mark = AT-LEAST-ONCE; consumers dedup on the event_id
carried in every payload.

The same thread tails agent_events by a cursor (those rows are best-effort
telemetry written outside any plan transaction — no delivered flag needed,
just forward progress) and forwards them as "agent.event".
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Callable

import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from src.api.sse import SSEBroker

log = structlog.get_logger(__name__)

_SELECT_UNDELIVERED = text(
    "SELECT id, event_id, plan_id, type, payload FROM outbox"
    " WHERE delivered_at IS NULL ORDER BY id LIMIT :batch"
)
_MARK_DELIVERED = text("UPDATE outbox SET delivered_at = :now WHERE id = :row_id")
_SELECT_AGENT_EVENTS = text(
    "SELECT id, event_id, plan_id, goal_id, task_id, run_id, attempt_id, "
    "attempt, seq, type, payload"
    " FROM agent_events WHERE id > :cursor ORDER BY id LIMIT :batch"
)


def relay_once(
    session_factory: sessionmaker[Session],
    broker: SSEBroker,
    agent_cursor: int,
    batch: int = 100,
) -> tuple[int, int]:
    """One relay pass. Returns (rows delivered, new agent-events cursor)."""
    delivered = 0
    with session_factory() as session:
        rows = session.execute(_SELECT_UNDELIVERED, {"batch": batch}).all()
        for row_id, event_id, plan_id, event_type, payload in rows:
            body = json.loads(payload)
            body["event_id"] = event_id  # the consumer dedup key, explicit
            # publish BEFORE mark: a crash between the two re-delivers
            # (at-least-once), never loses
            broker.publish(event_type, body)
            session.execute(
                _MARK_DELIVERED,
                {"now": datetime.now(timezone.utc).isoformat(), "row_id": row_id},
            )
            delivered += 1
        session.commit()

        agent_rows = session.execute(
            _SELECT_AGENT_EVENTS, {"cursor": agent_cursor, "batch": batch}
        ).all()
        for row in agent_rows:
            agent_cursor = max(agent_cursor, int(row[0]))
            broker.publish(
                "agent.event",
                {
                    "event_id": row[1],
                    "plan_id": row[2],
                    "goal_id": row[3],
                    "task_id": row[4],
                    "run_id": row[5],
                    "attempt_id": row[6],
                    "attempt": row[7],
                    "seq": row[8],
                    "type": row[9],
                    "payload": json.loads(row[10]),
                },
            )
    return delivered, agent_cursor


def run_outbox_relay(
    session_factory: sessionmaker[Session],
    broker: SSEBroker,
    should_stop: Callable[[], bool],
    poll_seconds: float = 0.5,
    batch: int = 100,
) -> None:
    """Thread body: poll until told to stop. Own connections throughout —
    never touches the request-scoped UnitOfWork sessions."""
    log.info("outbox_relay.started", poll_seconds=poll_seconds)
    agent_cursor = 0
    while not should_stop():
        try:
            delivered, agent_cursor = relay_once(session_factory, broker, agent_cursor, batch)
        except Exception:
            log.error("outbox_relay.pass_failed", exc_info=True)
            delivered = 0
        if delivered < batch:
            time.sleep(poll_seconds)
    log.info("outbox_relay.stopped")
