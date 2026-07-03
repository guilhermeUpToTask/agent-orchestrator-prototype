"""
src/infra/db/agent_event_sink.py — SqliteAgentEventSink (the AgentEventSink port).

Fine-grained agent runtime events are BEST-EFFORT telemetry: written on their
own connection, never inside the plan UnitOfWork transaction — a telemetry
hiccup must never roll back state, and state rollback must never lose the
record of what the agent actually did. INSERT OR IGNORE on event_id keeps
re-deliveries idempotent.
"""
from __future__ import annotations

import asyncio
import json

import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from src.domain.events.agent_events import AgentEvent
from src.infra.db._session import run_in_session

log = structlog.get_logger(__name__)

_INSERT_SQL = text(
    """
    INSERT OR IGNORE INTO agent_events
        (event_id, plan_id, task_id, attempt, seq, type, payload, occurred_at)
    VALUES (:event_id, :plan_id, :task_id, :attempt, :seq, :type, :payload,
            :occurred_at)
    """
)


class SqliteAgentEventSink:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    async def emit(self, event: AgentEvent) -> None:
        params = {
            "event_id": event.event_id,
            "plan_id": event.plan_id,
            "task_id": event.task_id,
            "attempt": event.attempt,
            "seq": event.seq,
            "type": event.type,
            "payload": json.dumps(event.payload),
            "occurred_at": event.occurred_at.isoformat(),
        }
        try:
            await asyncio.to_thread(
                run_in_session,
                self._sf,
                lambda s: s.execute(_INSERT_SQL, params),
            )
        except Exception:
            # best-effort: telemetry loss is logged, never propagated
            log.warning(
                "agent_event_sink.write_failed",
                event_id=event.event_id,
                type=event.type,
                exc_info=True,
            )
