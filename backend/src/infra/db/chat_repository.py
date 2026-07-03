"""
src/infra/db/chat_repository.py — SqliteChatRepository (the ChatStore port).

Conversation history for DISCOVERY/REPLANNING. Each append is its own short
transaction (run_in_session), never part of the plan UnitOfWork: chat is
display history, plan state is truth — neither may roll the other back.
"""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from src.domain.ports.reasoner_port import ChatMessage
from src.infra.db._session import run_in_session

_INSERT_SQL = text(
    """
    INSERT INTO plan_chat_messages (plan_id, role, content, meta, created_at)
    VALUES (:plan_id, :role, :content, :meta, :created_at)
    """
)

_SELECT_SQL = text(
    """
    SELECT role, content, meta, created_at
    FROM plan_chat_messages
    WHERE plan_id = :plan_id
    ORDER BY id
    """
)


class SqliteChatRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def append(self, plan_id: str, message: ChatMessage) -> None:
        params = {
            "plan_id": plan_id,
            "role": message.role,
            "content": message.content,
            "meta": json.dumps(message.meta),
            "created_at": message.created_at.isoformat(),
        }
        run_in_session(self._sf, lambda s: s.execute(_INSERT_SQL, params))

    def list(self, plan_id: str) -> list[ChatMessage]:
        def _query(s: Session) -> list[ChatMessage]:
            rows = s.execute(_SELECT_SQL, {"plan_id": plan_id}).all()
            return [
                ChatMessage(
                    role=row.role,
                    content=row.content,
                    meta=json.loads(row.meta),
                    created_at=datetime.fromisoformat(row.created_at),
                )
                for row in rows
            ]

        return run_in_session(self._sf, _query)
