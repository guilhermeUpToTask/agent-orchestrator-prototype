"""The plan claim under REAL contention — many engines, one database file.

The goal lease already had a two-thread race
(`test_goal_lease_repository.py::test_two_sqlite_repositories_racing_for_one_goal_have_one_winner`).
The PLAN claim — `claim_one_unit`, the thing that decides which worker advances
which plan — had none: every test of it drove one repository, serially. Phase
10A added these, and each worker gets its OWN engine so the race is between
connection pools the way two `orchestrate worker start` processes would be,
not between threads sharing one.

What is deliberately NOT claimed here: that multi-worker execution is
supported. It is not (see ROADMAP "multi-worker/multi-machine execution,
distributed claims, or Redis" — deferred). These lock the property the lease
exists for — a dead worker's plan is reclaimable by exactly one survivor, and
never by two.
"""

from __future__ import annotations

import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text

from agent_orchestrator.app.testing.fakes import FakeClock
from agent_orchestrator.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from agent_orchestrator.infra.db.engine import build_engine, make_session_factory
from agent_orchestrator.infra.db.plan_repository import SqlitePlanRepository
from agent_orchestrator.infra.db.tables import Base
from agent_orchestrator.infra.db.unit_of_work import SqliteUnitOfWork

pytestmark = pytest.mark.integration

WORKERS = 6
LEASE = 60


def _seed(db_url: str, plan_count: int) -> FakeClock:
    """`plan_count` claimable plans, each in its OWN project.

    One project owns exactly one plan and `uq_plans_project_id` enforces it — an
    earlier draft of this fixture reused one project id with `INSERT OR IGNORE`
    and silently seeded a single plan, which looked exactly like a claim bug.
    """
    engine = build_engine(db_url)
    Base.metadata.create_all(engine)
    clock = FakeClock()
    uow = SqliteUnitOfWork(make_session_factory(engine), clock)
    for i in range(plan_count):
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO projects (id, name, repo_url) VALUES (:p, :p, NULL)"),
                {"p": f"project-{i}"},
            )
        with uow:
            uow.plans.save(
                Plan(
                    id=f"plan-{i}",
                    project_id=f"project-{i}",
                    brief="contention",
                    phase=PlanPhase.RUNNING,
                )
            )
    return clock


def _race(db_url: str, clock: FakeClock, workers: int) -> list[Plan | None]:
    """Release `workers` claim attempts simultaneously, one engine each."""
    barrier = threading.Barrier(workers)
    repos = [
        SqlitePlanRepository(make_session_factory(build_engine(db_url)), clock)
        for _ in range(workers)
    ]

    def claim(index: int) -> Plan | None:
        barrier.wait()
        return repos[index].claim_one_unit(f"worker-{index}", LEASE)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(claim, range(workers)))


def test_one_plan_and_many_workers_has_exactly_one_winner(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'one.db'}"
    clock = _seed(db_url, plan_count=1)

    results = _race(db_url, clock, WORKERS)

    winners = [plan for plan in results if plan is not None]
    assert len(winners) == 1, f"{len(winners)} workers claimed the same plan"
    assert winners[0].id == "plan-0"


def test_no_plan_is_ever_claimed_by_two_workers(tmp_path) -> None:
    """More workers than plans: every plan goes out exactly once, and the
    workers left over are told there is nothing rather than handed a duplicate."""
    plan_count = 5
    db_url = f"sqlite:///{tmp_path / 'many.db'}"
    clock = _seed(db_url, plan_count=plan_count)

    results = _race(db_url, clock, WORKERS)

    claimed = [plan.id for plan in results if plan is not None]
    duplicated = {pid: n for pid, n in Counter(claimed).items() if n > 1}
    assert not duplicated, f"double-claimed: {duplicated}"
    assert len(claimed) == plan_count
    assert set(claimed) == {f"plan-{i}" for i in range(plan_count)}
    assert results.count(None) == WORKERS - plan_count


def test_an_expired_lease_is_reclaimed_by_exactly_one_survivor(tmp_path) -> None:
    """The reason the lease exists: the holder died, and the fleet recovers the
    plan once — not once per worker."""
    db_url = f"sqlite:///{tmp_path / 'expired.db'}"
    clock = _seed(db_url, plan_count=1)
    original = SqlitePlanRepository(make_session_factory(build_engine(db_url)), clock)
    assert original.claim_one_unit("worker-that-died", LEASE) is not None

    clock.advance(LEASE + 1)
    results = _race(db_url, clock, WORKERS)

    winners = [plan for plan in results if plan is not None]
    assert len(winners) == 1


def test_a_live_lease_is_not_stealable_by_the_fleet(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'live.db'}"
    clock = _seed(db_url, plan_count=1)
    holder = SqlitePlanRepository(make_session_factory(build_engine(db_url)), clock)
    assert holder.claim_one_unit("holder", LEASE) is not None

    clock.advance(LEASE - 1)  # still live
    results = _race(db_url, clock, WORKERS)

    assert all(plan is None for plan in results)


def test_a_displaced_worker_cannot_heartbeat_its_plan_back(tmp_path) -> None:
    """`heartbeat` returns None, so a displaced worker is not TOLD it lost the
    plan — the guard is that its renewal simply does not land. (The write it
    would go on to attempt is refused separately, by the version CAS in
    `save`.)"""
    db_url = f"sqlite:///{tmp_path / 'displaced.db'}"
    clock = _seed(db_url, plan_count=1)
    engine = build_engine(db_url)
    displaced = SqlitePlanRepository(make_session_factory(engine), clock)
    assert displaced.claim_one_unit("displaced", LEASE) is not None

    clock.advance(LEASE + 1)
    thief = SqlitePlanRepository(make_session_factory(build_engine(db_url)), clock)
    assert thief.claim_one_unit("thief", LEASE) is not None

    displaced.heartbeat("plan-0", "displaced")

    with engine.connect() as connection:
        holder, expires = connection.execute(
            text("SELECT claimed_by, lease_expires_at FROM plans WHERE id = 'plan-0'")
        ).one()
    assert holder == "thief"
    assert expires == int(clock.now().timestamp()) + LEASE
