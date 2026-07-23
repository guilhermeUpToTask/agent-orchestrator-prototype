"""Contract tests for the in-memory and SQLite goal-lease repositories."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from src.infra.db.engine import build_engine, make_session_factory
from src.infra.db.goal_lease_repository import SqliteGoalLeaseRepository
from src.infra.db.tables import Base
from src.infra.db.unit_of_work import SqliteUnitOfWork

from src.app.testing.fakes import (
    FakeClock,
    InMemoryGoalLeaseRepository,
    InMemoryOutbox,
    InMemoryPlanRepository,
    InMemoryUnitOfWork,
)


def _seed_running_plan(env) -> None:
    env.seed(
        Plan(
            project_id="project-1",
            id="p1",
            brief="goal lease contract",
            phase=PlanPhase.RUNNING,
        )
    )


def test_claim_rejects_a_live_lease_and_allows_expired_reclaim(env_factory) -> None:
    env = env_factory()
    _seed_running_plan(env)
    leases = env.uow.goal_leases

    assert leases.claim_one_ready_goal("p1", "g1", "w1", 60, env.clock.now())
    assert leases.is_claim_live("p1", "g1", env.clock.now())
    assert not leases.claim_one_ready_goal("p1", "g1", "w2", 60, env.clock.now())

    env.clock.advance(61)
    assert not leases.is_claim_live("p1", "g1", env.clock.now())
    assert leases.claim_one_ready_goal("p1", "g1", "w2", 60, env.clock.now())


def test_heartbeat_extends_only_the_current_workers_goal_lease(env_factory) -> None:
    env = env_factory()
    _seed_running_plan(env)
    leases = env.uow.goal_leases

    assert leases.claim_one_ready_goal("p1", "g1", "w1", 60, env.clock.now())
    env.clock.advance(50)
    assert not leases.heartbeat("p1", "g1", "w2", 60, env.clock.now())
    assert leases.heartbeat("p1", "g1", "w1", 60, env.clock.now())
    env.clock.advance(11)

    assert leases.is_claim_live("p1", "g1", env.clock.now())
    assert not leases.claim_one_ready_goal("p1", "g1", "w2", 60, env.clock.now())


def test_heartbeat_after_expiry_returns_false_without_extending_lease(env_factory) -> None:
    env = env_factory()
    _seed_running_plan(env)
    leases = env.uow.goal_leases

    assert leases.claim_one_ready_goal("p1", "g1", "w1", 60, env.clock.now())
    env.clock.advance(61)

    assert not leases.heartbeat("p1", "g1", "w1", 60, env.clock.now())
    assert leases.claim_one_ready_goal("p1", "g1", "w2", 60, env.clock.now())


def test_release_clears_only_the_current_workers_goal_lease(env_factory) -> None:
    env = env_factory()
    _seed_running_plan(env)
    leases = env.uow.goal_leases

    assert leases.claim_one_ready_goal("p1", "g1", "w1", 60, env.clock.now())
    leases.release("p1", "g1", "w2")
    assert not leases.claim_one_ready_goal("p1", "g1", "w2", 60, env.clock.now())

    leases.release("p1", "g1", "w1")
    assert not leases.is_claim_live("p1", "g1", env.clock.now())
    assert leases.claim_one_ready_goal("p1", "g1", "w2", 60, env.clock.now())


def test_shared_lease_repo_lets_a_second_uow_steal_after_expiry() -> None:
    """Two InMemoryUnitOfWork instances constructed over the SAME plan repo and
    SAME goal-lease repo simulate two worker processes sharing one SQLite DB
    file. Without a shared goal_leases instance this scenario is structurally
    untestable (each UoW would get its own private lease dict) — this is the
    gap a real double-claim bug exploited before it was caught."""
    clock = FakeClock()
    plans = InMemoryPlanRepository(clock)
    plans.add(Plan(project_id="project-1", id="p1", brief="cross-uow steal", phase=PlanPhase.RUNNING))
    goal_leases = InMemoryGoalLeaseRepository()

    uow_a = InMemoryUnitOfWork(plans, InMemoryOutbox(), goal_leases=goal_leases)
    uow_b = InMemoryUnitOfWork(plans, InMemoryOutbox(), goal_leases=goal_leases)
    assert uow_a.goal_leases is uow_b.goal_leases is goal_leases

    # worker A claims the goal; worker B's simultaneous claim attempt loses.
    assert uow_a.goal_leases.claim_one_ready_goal("p1", "g1", "worker-a", 60, clock.now())
    assert not uow_b.goal_leases.claim_one_ready_goal("p1", "g1", "worker-b", 60, clock.now())

    # worker A stalls/crashes; its lease expires.
    clock.advance(61)

    # worker B steals the now-expired lease through its OWN UoW instance.
    assert uow_b.goal_leases.claim_one_ready_goal("p1", "g1", "worker-b", 60, clock.now())
    # worker A's heartbeat must fail — it no longer holds the lease.
    assert not uow_a.goal_leases.heartbeat("p1", "g1", "worker-a", 60, clock.now())
    assert uow_b.goal_leases.is_claim_live("p1", "g1", clock.now())


@pytest.mark.integration
def test_two_sqlite_repositories_racing_for_one_goal_have_one_winner(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'goal-lease-race.db'}"
    first_engine = build_engine(db_url)
    Base.metadata.create_all(first_engine)
    first_factory = make_session_factory(first_engine)
    clock = FakeClock()
    uow = SqliteUnitOfWork(first_factory, clock)
    with uow:
        uow.plans.save(
            Plan(id="p1", brief="race", phase=PlanPhase.RUNNING)
        )

    second_engine = build_engine(db_url)
    first_repo = SqliteGoalLeaseRepository(first_factory)
    second_repo = SqliteGoalLeaseRepository(make_session_factory(second_engine))
    barrier = threading.Barrier(2)

    def race(repo: SqliteGoalLeaseRepository, worker_id: str) -> bool:
        barrier.wait()
        return repo.claim_one_ready_goal("p1", "g1", worker_id, 60, clock.now())

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(race, first_repo, "w1"),
            executor.submit(race, second_repo, "w2"),
        ]
        results = [future.result() for future in futures]

    first_engine.dispose()
    second_engine.dispose()
    assert sorted(results) == [False, True]
