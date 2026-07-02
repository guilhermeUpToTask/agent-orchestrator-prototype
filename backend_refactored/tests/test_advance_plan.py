"""End-to-end orchestration tests for advance_plan, using only in-memory doubles.
Proves the full loop: claim->decide->execute->persist->retry->recover, plus the
transactional outbox and check-before-act idempotency — with zero infrastructure."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import asyncio
import pytest

from domain.aggregates.planner_orchestrator import Plan, PlanPhase
from domain.entities.goal import Goal
from domain.entities.task import Task
from domain.entities.agent_spec import AgentSpec
from domain.value_objects.lifecycle import FailureKind, Status
from domain.value_objects.tasks_vos import TaskResult
from domain.policies.retry_policies import RetryPolicy

from application.use_cases.advance_plan import advance_plan
from application.use_cases.control import finish_review
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


def agent():
    return AgentSpec(
        id="a1",
        name="A",
        role="agent",
        model_role="agent",
        instructions="",
        default_retry=RetryPolicy(),
    )


def make_plan(tasks_per_goal=1, n_goals=1, retry_max=3):
    goals = []
    for gi in range(n_goals):
        tasks = [
            Task(
                id=f"g{gi}t{ti}",
                name=f"t{ti}",
                position=ti,
                description="",
                agent_id="a1",
            )
            for ti in range(tasks_per_goal)
        ]
        goals.append(
            Goal(id=f"g{gi}", name=f"g{gi}", position=gi, description="", tasks=tasks)
        )
    return Plan(
        id="p1",
        brief="b",
        phase=PlanPhase.RUNNING,
        retry_policy=RetryPolicy(max_attempts=retry_max),
        goals=goals,
    )


def harness(plan, script=None):
    repo = InMemoryPlanRepository()
    repo.add(plan)
    outbox = InMemoryOutbox()
    uow = InMemoryUnitOfWork(repo, outbox)
    runner = DummyAgentRunner(script or {})
    agents = InMemoryAgentRepository([agent()], default_id="a1")
    ws = NoOpWorkspace()
    sink = CollectingEventSink()
    clock = FakeClock()
    return repo, outbox, uow, runner, agents, ws, sink, clock


async def run_to_completion(plan, script=None, max_steps=50):
    """Drive the plan through RUNNING. Execution now exhausts into the REVIEW gate
    (paused); the human "finish" (finish_review) is what reaches DONE — modeled
    here so tests keep asserting the full RUNNING -> REVIEW -> DONE path."""
    repo, outbox, uow, runner, agents, ws, sink, clock = harness(plan, script)
    signal = "continue"
    steps = 0
    while signal in ("continue", "not_ready") and steps < max_steps:
        signal = await advance_plan("p1", uow, runner, agents, ws, sink, clock)
        if signal == "not_ready":
            clock.advance(120)  # model the worker waiting out the backoff gate
        steps += 1
    if signal == "paused" and repo.get("p1").phase == PlanPhase.REVIEW:
        finish_review("p1", uow)  # human closes the post-exec gate
        signal = "done"
    return signal, repo, outbox, runner, ws, sink


# ---- happy path ----
def test_single_task_completes():
    sig, repo, outbox, runner, ws, sink = asyncio.run(run_to_completion(make_plan()))
    assert sig == "done"
    final = repo.get("p1")
    assert final.phase == PlanPhase.DONE
    assert final.goals[0].tasks[0].status == Status.DONE
    assert runner.calls["g0t0"] == 1
    assert "TaskStarted" in outbox.types() and "TaskCompleted" in outbox.types()
    assert "PlanCompleted" in outbox.types()
    assert ws.committed and not ws.discarded  # workspace committed on success


def test_multiple_tasks_and_goals_sequential():
    plan = make_plan(tasks_per_goal=2, n_goals=2)
    sig, repo, outbox, runner, ws, sink = asyncio.run(run_to_completion(plan))
    assert sig == "done"
    final = repo.get("p1")
    assert all(t.status == Status.DONE for g in final.goals for t in g.tasks)
    assert outbox.types().count("TaskCompleted") == 4
    assert outbox.types().count("GoalCompleted") == 2


# ---- retry path ----
def test_retry_then_succeed():
    script = {"g0t0": DummyBehavior(fail_times=2, output="recovered")}
    sig, repo, outbox, runner, ws, sink = asyncio.run(run_to_completion(make_plan()))
    # default plan has no script; redo with script:
    plan = make_plan()
    sig, repo, outbox, runner, ws, sink = asyncio.run(run_to_completion(plan, script))
    assert sig == "done"
    final = repo.get("p1")
    assert final.goals[0].tasks[0].status == Status.DONE
    assert final.goals[0].tasks[0].attempt == 3  # 2 fails + 1 success
    assert runner.calls["g0t0"] == 3
    assert outbox.types().count("TaskRequeued") == 2  # two requeues
    assert (
        len(ws.discarded) == 2 and len(ws.committed) == 1
    )  # 2 failed runs discarded, 1 committed


def test_permanent_failure_halts_plan():
    script = {"g0t0": DummyBehavior(always_fail=True, fail_reason="boom")}
    plan = make_plan(retry_max=3)
    sig, repo, outbox, runner, ws, sink = asyncio.run(run_to_completion(plan, script))
    assert sig == "failed"
    final = repo.get("p1")
    assert final.phase == PlanPhase.FAILED
    assert final.goals[0].tasks[0].status == Status.FAILED
    # attempts: tries until exhausted (max_attempts=3) then terminal
    assert runner.calls["g0t0"] == 3
    assert "TaskFailedEvent" in outbox.types() and "PlanFailed" in outbox.types()


def test_non_retryable_fails_immediately():
    script = {
        "g0t0": DummyBehavior(
            always_fail=True,
            fail_reason="key rejected",
            fail_kind=FailureKind.AUTH_ERROR,
        )
    }
    plan = make_plan(retry_max=5)
    sig, repo, outbox, runner, ws, sink = asyncio.run(run_to_completion(plan, script))
    assert sig == "failed"
    assert runner.calls["g0t0"] == 1  # non-retryable -> no retries despite max=5


# ---- idempotency / crash recovery ----
def test_check_before_act_skips_completed_task():
    """Simulate a crash after the agent ran but before txn2: the task is RUNNING
    with a result already set. advance_plan must finalize WITHOUT calling the agent."""
    plan = make_plan()
    plan.goals[0].tasks[0].status = Status.RUNNING
    plan.goals[0].tasks[0].result = TaskResult.success("already happened")
    plan.goals[0].status = Status.RUNNING
    sig, repo, outbox, runner, ws, sink = asyncio.run(run_to_completion(plan))
    assert sig == "done"
    assert "g0t0" not in runner.calls  # agent NOT re-invoked
    assert repo.get("p1").goals[0].tasks[0].status == Status.DONE


# ---- transactional outbox: rollback discards staged events ----
def test_outbox_rolls_back_on_failed_transaction():
    from domain.errors.tasks_errors import StaleVersionError

    repo = InMemoryPlanRepository()
    repo.add(make_plan())
    outbox = InMemoryOutbox()
    uow = InMemoryUnitOfWork(repo, outbox)
    # force a failure inside the with-block; assert no events leak
    try:
        with uow:
            uow.outbox.add(type("E", (), {"event_type": "Ghost"})())  # staged
            raise StaleVersionError("p1", 0, 1)
    except StaleVersionError:
        pass
    assert outbox.events == []  # staged event was rolled back, not committed


# ---- agent events flow to the sink, tagged by attempt ----
def test_agent_events_streamed_and_tagged():
    script = {"g0t0": DummyBehavior(emit_events=3)}
    plan = make_plan()
    sig, repo, outbox, runner, ws, sink = asyncio.run(run_to_completion(plan, script))
    assert len(sink.events) == 3
    assert all(e.task_id == "g0t0" for e in sink.events)
    assert [e.seq for e in sink.events] == [0, 1, 2]


# ---- missing agent fails fast (reactive safety net) ----
def test_missing_agent_raises_before_running():
    from domain.errors.agent_errors import AgentNotFoundError

    plan = make_plan()
    plan.goals[0].tasks[0].agent_id = "ghost"  # not in registry
    repo = InMemoryPlanRepository()
    repo.add(plan)
    uow = InMemoryUnitOfWork(repo, InMemoryOutbox())
    runner = DummyAgentRunner()
    agents = InMemoryAgentRepository([agent()], default_id="a1")
    with pytest.raises(AgentNotFoundError):
        asyncio.run(
            advance_plan(
                "p1",
                uow,
                runner,
                agents,
                NoOpWorkspace(),
                CollectingEventSink(),
                FakeClock(),
            )
        )
    assert "g0t0" not in runner.calls  # never ran
