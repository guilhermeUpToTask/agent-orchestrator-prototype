"""Edge-case hardening: degenerate plans, terminal-state guards, multi-failure
sequences, backoff wiring, max_steps safety, and skip handling. These are the
states a real run actually reaches."""

import sys, os, asyncio, pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from domain.aggregates.planner_orchestrator import Plan, PlanPhase
from domain.entities.goal import Goal
from domain.entities.task import Task
from domain.entities.agent_spec import AgentSpec
from domain.value_objects.tasks_vos import Status, TaskResult
from domain.policies.retry_policies import RetryPolicy
from datetime import datetime, timezone
from domain.services.navigation import next_action

from application.use_cases.advance_plan import advance_plan
from application.use_cases.run_worker import drive_plan
from application.testing.fakes import (
    FakeClock,
    InMemoryPlanRepository,
    InMemoryOutbox,
    InMemoryUnitOfWork,
    InMemoryAgentRepository,
    NoOpWorkspace,
    CollectingEventSink,
    DummyAgentRunner,
    DummyBehavior,
)

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def agent():
    return AgentSpec(
        id="a1",
        name="A",
        role="agent",
        model_role="agent",
        instructions="",
        default_retry=RetryPolicy(),
    )


def harness(plan, script=None):
    repo = InMemoryPlanRepository()
    repo.add(plan)
    uow = InMemoryUnitOfWork(repo, InMemoryOutbox())
    runner = DummyAgentRunner(script or {})
    agents = InMemoryAgentRepository([agent()], default_id="a1")
    return (
        repo,
        uow,
        runner,
        agents,
        NoOpWorkspace(),
        CollectingEventSink(),
        FakeClock(),
    )


async def drive(repo, uow, runner, agents, ws, sink, clock):
    # model a worker that waits out backoff gates: advance clock on not_ready
    sig = await drive_plan("p1", uow, runner, agents, ws, sink, clock, "w1")
    while sig == "not_ready":
        clock.advance(300)
        sig = await drive_plan("p1", uow, runner, agents, ws, sink, clock, "w1")
    return sig


# ===== DEGENERATE PLANS =====
def test_empty_plan_no_goals_completes_immediately():
    p = Plan(id="p1", brief="b", phase=PlanPhase.EXECUTING, goals=[])
    assert next_action([], _NOW) is None
    repo, uow, runner, agents, ws, sink, clock = harness(p)
    sig = asyncio.run(drive(repo, uow, runner, agents, ws, sink, clock))
    assert sig == "done" and repo.get("p1").phase == PlanPhase.DONE


def test_goal_with_no_tasks_closes_cleanly():
    g = Goal(id="g1", name="g1", position=0, description="", tasks=[])
    p = Plan(id="p1", brief="b", phase=PlanPhase.EXECUTING, goals=[g])
    # next_action: goal has no tasks, no failed task -> (goal, None) to close
    go, second = next_action([g], _NOW)
    assert second is None
    repo, uow, runner, agents, ws, sink, clock = harness(p)
    sig = asyncio.run(drive(repo, uow, runner, agents, ws, sink, clock))
    assert sig == "done"
    assert repo.get("p1").goals[0].status == Status.DONE


def test_mix_of_empty_and_populated_goals():
    g1 = Goal(id="g1", name="g1", position=0, description="", tasks=[])  # empty
    g2 = Goal(
        id="g2",
        name="g2",
        position=1,
        description="",
        tasks=[Task(id="g2t0", name="t", position=0, description="", agent_id="a1")],
    )
    p = Plan(id="p1", brief="b", phase=PlanPhase.EXECUTING, goals=[g1, g2])
    repo, uow, runner, agents, ws, sink, clock = harness(p)
    sig = asyncio.run(drive(repo, uow, runner, agents, ws, sink, clock))
    assert sig == "done"
    assert repo.get("p1").goals[0].status == Status.DONE  # empty goal closed
    assert repo.get("p1").goals[1].tasks[0].status == Status.DONE


# ===== TERMINAL-STATE GUARDS =====
def test_advance_already_done_plan_returns_done():
    p = Plan(id="p1", brief="b", phase=PlanPhase.DONE, goals=[])
    repo, uow, runner, agents, ws, sink, clock = harness(p)
    sig = asyncio.run(advance_plan("p1", uow, runner, agents, ws, sink, clock))
    assert sig == "done"


def test_advance_already_failed_plan_returns_failed():
    p = Plan(id="p1", brief="b", phase=PlanPhase.FAILED, goals=[])
    repo, uow, runner, agents, ws, sink, clock = harness(p)
    sig = asyncio.run(advance_plan("p1", uow, runner, agents, ws, sink, clock))
    assert sig == "failed"


def test_non_executing_phase_pauses_or_continues():
    # AWAITING_REVIEW with default pause_after={ENRICHING} -> not in pause set -> 'paused'?
    p = Plan(id="p1", brief="b", phase=PlanPhase.AWAITING_REVIEW, goals=[])
    repo, uow, runner, agents, ws, sink, clock = harness(p)
    sig = asyncio.run(advance_plan("p1", uow, runner, agents, ws, sink, clock))
    assert sig in ("paused", "continue")  # AWAITING_REVIEW is not EXECUTING


# ===== MULTI-FAILURE SEQUENCES =====
def test_two_different_tasks_each_retry_then_succeed():
    g = Goal(
        id="g1",
        name="g1",
        position=0,
        description="",
        tasks=[
            Task(id="t0", name="t0", position=0, description="", agent_id="a1"),
            Task(id="t1", name="t1", position=1, description="", agent_id="a1"),
        ],
    )
    p = Plan(id="p1", brief="b", phase=PlanPhase.EXECUTING, goals=[g])
    script = {"t0": DummyBehavior(fail_times=1), "t1": DummyBehavior(fail_times=1)}
    repo, uow, runner, agents, ws, sink, clock = harness(p, script)
    sig = asyncio.run(drive(repo, uow, runner, agents, ws, sink, clock))
    assert sig == "done"
    assert runner.calls["t0"] == 2 and runner.calls["t1"] == 2
    assert all(t.status == Status.DONE for t in repo.get("p1").goals[0].tasks)


def test_first_task_succeeds_second_permanently_fails_halts():
    g = Goal(
        id="g1",
        name="g1",
        position=0,
        description="",
        tasks=[
            Task(id="t0", name="t0", position=0, description="", agent_id="a1"),
            Task(id="t1", name="t1", position=1, description="", agent_id="a1"),
        ],
    )
    p = Plan(
        id="p1",
        brief="b",
        phase=PlanPhase.EXECUTING,
        retry_policy=RetryPolicy(max_attempts=2),
        goals=[g],
    )
    script = {"t1": DummyBehavior(always_fail=True, fail_reason="boom")}
    repo, uow, runner, agents, ws, sink, clock = harness(p, script)
    sig = asyncio.run(drive(repo, uow, runner, agents, ws, sink, clock))
    assert sig == "failed"
    final = repo.get("p1")
    assert final.goals[0].tasks[0].status == Status.DONE  # first succeeded
    assert final.goals[0].tasks[1].status == Status.FAILED  # second exhausted
    assert final.phase == PlanPhase.FAILED


# ===== BACKOFF WIRING (the fix) =====


def test_backoff_policy_schedule_and_cap():
    rp = RetryPolicy(
        initial_backoff_seconds=10, backoff_multiplier=10, max_backoff_seconds=15
    )
    assert rp.backoff_for(1) == 0.0  # first try, no backoff
    assert rp.backoff_for(2) == 10.0  # first retry -> initial
    assert rp.backoff_for(3) == 15.0  # 100 capped to 15
    assert rp.backoff_for(5) == 15.0


# ===== max_steps SAFETY =====
def test_max_steps_prevents_runaway():
    # craft a plan that would loop: a single task that always fails but is always
    # retryable (max_attempts huge) -> drive_plan must stop at max_steps, not hang
    g = Goal(
        id="g1",
        name="g1",
        position=0,
        description="",
        tasks=[Task(id="t0", name="t", position=0, description="", agent_id="a1")],
    )
    p = Plan(
        id="p1",
        brief="b",
        phase=PlanPhase.EXECUTING,
        retry_policy=RetryPolicy(max_attempts=10**9),
        goals=[g],
    )
    script = {"t0": DummyBehavior(always_fail=True)}
    repo, uow, runner, agents, ws, sink, clock = harness(p, script)
    # use zero backoff so retries are immediately ready -> exercises max_steps cap
    from domain.policies.retry_policies import RetryPolicy as _RP

    repo.get  # noqa
    plan2 = repo.get("p1")
    plan2.retry_policy = _RP(max_attempts=10**9, initial_backoff_seconds=0)
    # re-seed with zero-backoff policy
    repo._store["p1"].retry_policy = _RP(max_attempts=10**9, initial_backoff_seconds=0)
    sig = asyncio.run(
        drive_plan("p1", uow, runner, agents, ws, sink, clock, "w1", max_steps=5)
    )
    assert sig == "continue"  # stopped at cap, not terminal, didn't hang
    assert runner.calls["t0"] == 5  # exactly max_steps attempts, then bailed


# ===== SKIP HANDLING =====
def test_skipped_tasks_are_passed_over():
    t0 = Task(id="t0", name="t0", position=0, description="", agent_id="a1")
    t0.status = Status.SKIPPED
    t1 = Task(id="t1", name="t1", position=1, description="", agent_id="a1")
    g = Goal(id="g1", name="g1", position=0, description="", tasks=[t0, t1])
    p = Plan(id="p1", brief="b", phase=PlanPhase.EXECUTING, goals=[g])
    repo, uow, runner, agents, ws, sink, clock = harness(p)
    sig = asyncio.run(drive(repo, uow, runner, agents, ws, sink, clock))
    assert sig == "done"
    assert "t0" not in runner.calls  # skipped, never run
    assert runner.calls.get("t1") == 1  # only the non-skipped one ran


# ===== IDEMPOTENT CREATE under concurrency-ish repeated calls =====
def test_check_before_act_does_not_emit_duplicate_taskstarted():
    # crash-recovery finalize path must NOT emit TaskStarted again (only TaskCompleted)
    t = Task(id="t0", name="t", position=0, description="", agent_id="a1")
    t.status = Status.RUNNING
    t.result = TaskResult.success("recovered")
    g = Goal(
        id="g1", name="g1", position=0, description="", status=Status.RUNNING, tasks=[t]
    )
    p = Plan(id="p1", brief="b", phase=PlanPhase.EXECUTING, goals=[g])
    repo, uow, runner, agents, ws, sink, clock = harness(p)
    asyncio.run(drive(repo, uow, runner, agents, ws, sink, clock))
    types = uow.outbox.types()
    assert "TaskStarted" not in types  # recovery path skips start
    assert "TaskCompleted" in types
