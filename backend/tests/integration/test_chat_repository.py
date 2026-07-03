"""SqliteChatRepository: per-plan ordering, plan isolation, meta round-trip.
The in-memory fake (InMemoryChatStore) must mirror these exact semantics, so
both run the same assertions."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from src.app.testing.fakes import InMemoryChatStore
from src.domain.ports.reasoner_port import ChatMessage
from src.infra.db.chat_repository import SqliteChatRepository
from src.infra.db.engine import build_engine, make_session_factory
from src.infra.db.tables import Base

pytestmark = pytest.mark.integration

T0 = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(params=["sqlite", "memory"])
def store(request, tmp_path):
    if request.param == "memory":
        return InMemoryChatStore()
    engine = build_engine(f"sqlite:///{tmp_path / 'chat.db'}")
    Base.metadata.create_all(engine)
    sf = make_session_factory(engine)
    # chat rows FK onto plans — seed the plan rows the tests reference
    with sf() as s:
        for pid in ("p1", "p2"):
            s.execute(
                text(
                    "INSERT INTO plans (id, version, phase, iteration, data,"
                    " created_at, updated_at)"
                    " VALUES (:id, 1, 'discovery', 1, '{}', :t, :t)"
                ),
                {"id": pid, "t": T0.isoformat()},
            )
        s.commit()
    return SqliteChatRepository(sf)


def _msg(role: str, content: str, **meta) -> ChatMessage:
    return ChatMessage(role=role, content=content, created_at=T0, meta=meta)


def test_append_and_list_preserve_order(store):
    store.append("p1", _msg("user", "first"))
    store.append("p1", _msg("assistant", "second"))
    store.append("p1", _msg("user", "third"))

    assert [(m.role, m.content) for m in store.list("p1")] == [
        ("user", "first"),
        ("assistant", "second"),
        ("user", "third"),
    ]


def test_plans_are_isolated(store):
    store.append("p1", _msg("user", "for p1"))
    store.append("p2", _msg("user", "for p2"))

    assert [m.content for m in store.list("p1")] == ["for p1"]
    assert [m.content for m in store.list("p2")] == ["for p2"]
    assert store.list("unknown-plan") == []


def test_meta_and_timestamp_round_trip(store):
    store.append("p1", _msg("assistant", "roadmap committed", committed=True, turns=3))

    (msg,) = store.list("p1")
    assert msg.meta == {"committed": True, "turns": 3}
    assert msg.created_at == T0
