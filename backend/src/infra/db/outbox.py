"""
src/infra/db/outbox.py — SqliteOutbox (the Outbox port).

add() INSERTs on the UnitOfWork's live session: the open transaction IS the
staging area (no in-memory buffer needed) — commit persists state + events
atomically; rollback discards both. That is the transactional-outbox guarantee
the in-memory fake simulates with its staged list.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.domain.events.base import DomainEvent

_INSERT_SQL = text(
    """
    INSERT INTO outbox (event_id, plan_id, type, payload, occurred_at)
    VALUES (:event_id, :plan_id, :type, :payload, :occurred_at)
    """
)


class SqliteOutbox:
    def __init__(self) -> None:
        self._session: Session | None = None

    # --- UnitOfWork binding ---
    def bind(self, session: Session) -> None:
        self._session = session

    def unbind(self) -> None:
        self._session = None

    def add(self, event: DomainEvent) -> None:
        if self._session is None:
            raise RuntimeError("SqliteOutbox used outside a UnitOfWork transaction")
        self._session.execute(
            _INSERT_SQL,
            {
                "event_id": event.event_id,
                "plan_id": event.plan_id,
                "type": event.event_type,
                "payload": event.model_dump_json(),
                "occurred_at": event.occurred_at.isoformat(),
            },
        )
