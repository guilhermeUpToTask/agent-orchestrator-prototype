"""Tests proving the durable backoff gate (retry_not_before) works — including
the property that distinguishes it from an in-memory sleep: it SURVIVES across
worker handoff (crash recovery). The env-based tests run on the in-memory fakes
AND the real SQLite UoW (the gate persisted inside the plan JSON document) —
backoff-gate-survives-crash on real SQLite is part of the integration truth-test."""

import asyncio
from datetime import datetime, timedelta, timezone

from src.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from src.domain.entities.goal import Goal
from src.domain.entities.task import Task
from src.domain.value_objects.lifecycle import Status
from src.domain.policies.retry_policies import RetryPolicy
from src.domain.services.navigation import next_action, NOT_READY

from src.app.use_cases.advance_plan import advance_plan
from src.app.testing.fakes import DummyBehavior

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def one_task_plan(retry_max=3, initial_backoff=10.0):
    g = Goal(
        id="g1",
        name="g1",
        position=0,
        description="",
        tasks=[Task(id="t0", name="t", position=0, description="", agent_id="a1")],
    )
    return Plan(
        id="p1",
        brief="b",
        phase=PlanPhase.RUNNING,
        retry_policy=RetryPolicy(
            max_attempts=retry_max,
            initial_backoff_seconds=initial_backoff,
            backoff_multiplier=2.0,
        ),
        goals=[g],
    )


# ===== NAVIGATION: backoff is a readiness condition (pure domain, no env) =====
def test_scan_skips_task_gated_in_future_returns_not_ready():
    t = Task(
        id="t0",
        name="t",
        position=0,
        description="",
        retry_not_before=NOW + timedelta(seconds=30),
    )
    g = Goal(id="g1", name="g", position=0, description="", tasks=[t])
    assert next_action([g], NOW) == NOT_READY  # gated -> not ready
    assert (
        next_action([g], NOW + timedelta(seconds=31)) != NOT_READY
    )  # gate expired -> runnable


def test_scan_runs_ungated_task_even_if_another_is_gated():
    gated = Task(
        id="t0",
        name="t0",
        position=0,
        description="",
        retry_not_before=NOW + timedelta(seconds=99),
    )
    ready = Task(id="t1", name="t1", position=1, description="")
    g = Goal(id="g1", name="g", position=0, description="", tasks=[gated, ready])
    # t0 gated -> skipped; t1 ready -> returned (backoff doesn't block other work)
    goal, task = next_action([g], NOW)
    assert task.id == "t1"


def test_not_ready_distinct_from_done():
    # a plan with only a gated task is NOT done — it's waiting
    t = Task(
        id="t0",
        name="t",
        position=0,
        description="",
        retry_not_before=NOW + timedelta(seconds=10),
    )
    g = Goal(id="g1", name="g", position=0, description="", tasks=[t])
    assert next_action([g], NOW) == NOT_READY
    # vs a plan whose only task is DONE -> None (truly complete)
    t2 = Task(id="t0", name="t", position=0, description="")
    t2.status = Status.DONE
    g2 = Goal(
        id="g2", name="g", position=0, description="", status=Status.DONE, tasks=[t2]
    )
    assert next_action([g2], NOW) is None


# ===== REQUEUE sets a durable gate =====
def test_requeue_sets_retry_not_before(env_factory):
    env = env_factory({"t0": DummyBehavior(always_fail=True)})
    env.seed(one_task_plan(initial_backoff=10.0))
    # one advance: runs t0 (attempt 1), fails, requeues with a gate
    asyncio.run(advance_plan("p1", *env.args))
    t = env.stored("p1").goals[0].tasks[0]
    assert t.status == Status.PENDING
    assert t.retry_not_before is not None
    # gate = now + backoff_for(attempt 2) = now + 10s
    assert t.retry_not_before == env.clock.now() + timedelta(seconds=10)


def test_advance_returns_not_ready_while_gated(env_factory):
    env = env_factory({"t0": DummyBehavior(always_fail=True)})
    env.seed(one_task_plan(initial_backoff=10.0))
    asyncio.run(advance_plan("p1", *env.args))  # fail+requeue
    # immediately try again WITHOUT advancing the clock -> gated -> not_ready
    sig = asyncio.run(advance_plan("p1", *env.args))
    assert sig == "not_ready"
    assert env.runner.calls["t0"] == 1  # NOT re-run while gated


def test_task_runs_again_after_gate_expires(env_factory):
    env = env_factory({"t0": DummyBehavior(fail_times=1)})  # fail once, then succeed
    env.seed(one_task_plan(retry_max=3, initial_backoff=10.0))
    asyncio.run(advance_plan("p1", *env.args))  # attempt1 fail+gate
    assert asyncio.run(advance_plan("p1", *env.args)) == "not_ready"
    env.clock.advance(10)  # cross the gate
    asyncio.run(advance_plan("p1", *env.args))  # attempt2 runs -> success
    final = env.stored("p1").goals[0].tasks[0]
    assert final.status == Status.DONE and env.runner.calls["t0"] == 2


# ===== THE KEY PROPERTY: gate survives worker handoff (crash recovery) =====
def test_backoff_gate_survives_worker_crash_and_reclaim(env_factory):
    """An in-memory sleep would be LOST when the worker dies. The durable gate is
    persisted, so a DIFFERENT worker that reclaims the plan still honors it."""
    env = env_factory({"t0": DummyBehavior(always_fail=True)})
    env.seed(one_task_plan(retry_max=3, initial_backoff=60.0))

    # worker-1 runs the task, it fails, gets requeued with a 60s gate, then w1 "dies"
    start = env.clock.now()
    asyncio.run(advance_plan("p1", *env.args))
    gated = env.stored("p1").goals[0].tasks[0]
    assert gated.retry_not_before == start + timedelta(seconds=60)

    # worker-2 reclaims almost immediately (5s later) — the gate must STILL hold
    env.clock.advance(5)
    sig = asyncio.run(advance_plan("p1", *env.args))
    assert sig == "not_ready"  # gate survived the handoff
    assert env.runner.calls["t0"] == 1  # NOT re-run early

    # only after the gate expires does the reclaiming worker run it
    env.clock.advance(60)
    asyncio.run(advance_plan("p1", *env.args))
    assert env.runner.calls["t0"] == 2  # now it ran


# ===== start() clears the gate =====
def test_start_clears_retry_gate():
    t = Task(
        id="t0",
        name="t",
        position=0,
        description="",
        retry_not_before=NOW + timedelta(seconds=5),
    )
    t.start()
    assert t.retry_not_before is None  # running clears the gate
