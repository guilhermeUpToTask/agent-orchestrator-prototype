"""WorkerRegistry: the `workers` table records that a worker is alive.

Not plan-scoped (a worker outlives every plan), upserted by worker_id rather
than appended (a restarted worker replaces its own row), and best-effort:
a write failure is logged and swallowed, never propagated — mirrors
`SqliteAgentEventSink` (`praxis_orchestrator/infra/db/agent_event_sink.py`)."""

from __future__ import annotations

import asyncio
import time

import pytest

from praxis_orchestrator.infra.db.engine import build_engine, make_session_factory
from praxis_orchestrator.infra.db.tables import Base
from praxis_orchestrator.infra.db.worker_registry import WorkerRegistry

pytestmark = pytest.mark.integration


def _registry(tmp_path, name: str = "workers.db") -> WorkerRegistry:
    engine = build_engine(f"sqlite:///{tmp_path / name}")
    Base.metadata.create_all(engine)
    return WorkerRegistry(make_session_factory(engine))


def test_first_beat_inserts_a_row_with_started_at_equal_to_last_seen_at(tmp_path):
    registry = _registry(tmp_path)

    asyncio.run(
        registry.beat(
            "worker-1",
            mode="dry-run",
            poll_seconds=1.0,
            lease_seconds=60,
            max_concurrent_goals=2,
            inflight_goals=0,
        )
    )

    rows = registry.list_workers()
    assert len(rows) == 1
    row = rows[0]
    assert row.worker_id == "worker-1"
    assert row.mode == "dry-run"
    assert row.started_at == row.last_seen_at
    assert row.poll_seconds == 1.0
    assert row.lease_seconds == 60
    assert row.max_concurrent_goals == 2
    assert row.inflight_goals == 0


def test_second_beat_from_same_worker_id_updates_the_row_in_place(tmp_path):
    registry = _registry(tmp_path)

    asyncio.run(
        registry.beat(
            "worker-1",
            mode="dry-run",
            poll_seconds=1.0,
            lease_seconds=60,
            max_concurrent_goals=2,
            inflight_goals=0,
        )
    )
    first = registry.list_workers()[0]

    # datetime.now(timezone.utc).isoformat() carries microsecond resolution;
    # a short real sleep guarantees the second beat's timestamp is strictly
    # later so "last_seen_at advanced" is not a coin flip on a fast machine.
    time.sleep(0.01)

    asyncio.run(
        registry.beat(
            "worker-1",
            mode="real",
            poll_seconds=2.5,
            lease_seconds=90,
            max_concurrent_goals=4,
            inflight_goals=3,
        )
    )

    rows = registry.list_workers()
    assert len(rows) == 1, "a restarted worker replaces its own row, never appends"
    second = rows[0]
    assert second.worker_id == "worker-1"
    assert second.started_at == first.started_at, "boot time is preserved across beats"
    assert second.last_seen_at > first.last_seen_at, "liveness timestamp advances"
    assert second.mode == "real"
    assert second.poll_seconds == 2.5
    assert second.lease_seconds == 90
    assert second.max_concurrent_goals == 4
    assert second.inflight_goals == 3


def test_two_different_worker_ids_produce_two_rows(tmp_path):
    registry = _registry(tmp_path)

    asyncio.run(
        registry.beat(
            "worker-1",
            mode="dry-run",
            poll_seconds=1.0,
            lease_seconds=60,
            max_concurrent_goals=2,
            inflight_goals=0,
        )
    )
    asyncio.run(
        registry.beat(
            "worker-2",
            mode="dry-run",
            poll_seconds=1.0,
            lease_seconds=60,
            max_concurrent_goals=2,
            inflight_goals=1,
        )
    )

    rows = {row.worker_id: row for row in registry.list_workers()}
    assert set(rows) == {"worker-1", "worker-2"}
    assert rows["worker-2"].inflight_goals == 1


def test_beat_against_a_broken_engine_is_swallowed_not_raised(tmp_path):
    """Best-effort telemetry: a write failure must never propagate.

    Replacing the database file with a directory forces sqlite3 to raise
    "unable to open database file" on the next connection attempt — a
    reliable, permission-independent way to break the underlying engine
    (chmod-based denial does not work when tests run as root)."""
    db_path = tmp_path / "broken.db"
    engine = build_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    engine.dispose()
    db_path.unlink()
    db_path.mkdir()

    registry = WorkerRegistry(session_factory)

    result = asyncio.run(
        registry.beat(
            "worker-1",
            mode="dry-run",
            poll_seconds=1.0,
            lease_seconds=60,
            max_concurrent_goals=2,
            inflight_goals=0,
        )
    )

    assert result is None
