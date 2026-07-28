"""`DELETE /api/plans/{id}` must leave nothing behind — proved against SQLite.

The unit suite proves the aggregate is gone on both backends. This proves the
part only the real database can answer: that every table carrying rows for that
plan is empty afterwards.

Migration 0015 made every plan-scoped table declare ON DELETE CASCADE, so the
adapter issues one `DELETE FROM plans` and the database collects the rest. Before
it, two tables had a foreign key with no ON DELETE (the delete was REJECTED) and
two carried a `plan_id` with no foreign key at all (rows were silently orphaned).

`test_every_plan_scoped_table_cascades` is the guard that keeps this true: a new
table holding plan rows has to opt out deliberately rather than be forgotten.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.app.use_cases.delete_plan import delete_plan
from src.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from src.domain.entities.goal import Goal
from src.domain.entities.task import Task
from src.infra.db.plan_repository import SqlitePlanRepository
from src.infra.db.tables import Base

from tests.support import make_sqlite_env

pytestmark = pytest.mark.integration

# Had no working cascade before migration 0015 — the regression cases.
FORMERLY_LEAKING = ("plan_requests", "plan_chat_messages", "outbox", "agent_events")

PLAN_SCOPED = (
    *FORMERLY_LEAKING,
    "goal_leases",
    "execution_runs",
    "execution_attempts",
    "planning_operations",
    "planning_artifacts",
    "goal_promotions",
)


def _plan(plan_id: str = "p1") -> Plan:
    return Plan(
        project_id="project-1",
        id=plan_id,
        brief="b",
        phase=PlanPhase.RUNNING,
        goals=[
            Goal(
                id="g1",
                name="g1",
                position=0,
                description="",
                tasks=[Task(id="t0", name="t0", position=0, description="", agent_id="a1")],
            )
        ],
    )


def test_no_row_anywhere_survives_the_delete(tmp_path: Path) -> None:
    db_path = tmp_path / "orchestrator.db"
    env = make_sqlite_env(db_path)
    env.seed(_plan())

    with env.uow:
        env.uow.plans.bind_request_id("req-1", "p1")
    # The lease methods run on their OWN session by design, so they must not be
    # called inside a UnitOfWork block that already holds a write lock.
    env.uow.plans.claim_one_unit("worker-1", lease_seconds=300)
    env.uow.plans.release("p1", "worker-1")

    _write(
        db_path,
        "INSERT INTO outbox (event_id, plan_id, type, payload, occurred_at) "
        "VALUES ('o1', 'p1', 'TaskStarted', '{}', '2026-07-27T00:00:00')",
        "INSERT INTO plan_chat_messages (plan_id, role, content, meta, created_at) "
        "VALUES ('p1', 'user', 'hi', '{}', '2026-07-27T00:00:00')",
        "INSERT INTO agent_events (event_id, plan_id, attempt, seq, type, source, quality, "
        "schema_version, payload, occurred_at, recorded_at) "
        "VALUES ('ae1', 'p1', 1, 1, 'agent.started', 'legacy', 'legacy_unknown', 0, '{}', "
        "'2026-07-27T00:00:00', '2026-07-27T00:00:00')",
    )

    before = {t: _count(db_path, t) for t in FORMERLY_LEAKING}
    assert all(before.values()), f"the fixture must populate every formerly-leaking table: {before}"

    delete_plan("p1", env.uow)

    leftovers = {
        table: count
        for table in (*PLAN_SCOPED, "plans")
        if (count := _count(db_path, table)) > 0
    }
    assert leftovers == {}, f"rows survived the delete: {leftovers}"


def test_every_plan_scoped_table_cascades() -> None:
    """Schema-drift guard.

    A new table holding a `plan_id` is exactly how "no leftovers" silently stops
    being true. With the rule in the schema, the delete either cascades or fails
    loudly — never succeeds while orphaning rows.
    """
    offenders: list[str] = []
    for table in Base.metadata.sorted_tables:
        if table.name == "plans" or "plan_id" not in table.columns:
            continue
        cascades = any(
            fk.column.table.name == "plans" and (fk.ondelete or "").upper() == "CASCADE"
            for fk in table.foreign_keys
        )
        if not cascades:
            offenders.append(table.name)

    assert not offenders, (
        "these tables hold plan-scoped rows without ON DELETE CASCADE, so deleting a "
        f"plan would orphan or reject them; add the foreign key in a migration: {offenders}"
    )


def test_the_adapter_relies_on_the_cascade() -> None:
    """The adapter must NOT re-grow a hand-maintained sweep list: that is the
    thing migration 0015 replaced, and a stale list is worse than none."""
    import inspect

    source = inspect.getsource(SqlitePlanRepository.delete)
    for table in FORMERLY_LEAKING:
        assert f'"{table}"' not in source, (
            f"{table} is swept explicitly again — the schema cascade should cover it"
        )


def _connect(db_path: Path) -> sqlite3.Connection:
    """A plain stdlib connection for the raw reads and writes this test needs.

    Deliberately NOT a second SQLAlchemy engine: every engine re-runs
    `PRAGMA journal_mode=WAL` on connect, which needs a brief exclusive lock, and
    a handful of throwaway engines alongside the UnitOfWork exhausts its
    lock-retry budget. The file's WAL mode is already set by the env's engine.
    """
    return sqlite3.connect(db_path, timeout=5)


def _write(db_path: Path, *statements: str) -> None:
    connection = _connect(db_path)
    try:
        with connection:
            for statement in statements:
                connection.execute(statement)
    finally:
        connection.close()


def _count(db_path: Path, table: str) -> int:
    connection = _connect(db_path)
    try:
        return int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
    finally:
        connection.close()
