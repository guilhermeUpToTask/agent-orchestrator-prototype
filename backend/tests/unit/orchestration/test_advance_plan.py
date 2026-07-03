"""End-to-end orchestration tests for advance_plan. Runs against the in-memory
doubles AND (via the env_factory parametrization) the REAL SQLite UnitOfWork —
the latter is the integration truth-test proving the transactional outbox and
check-before-act idempotency hold on real transactions."""

import asyncio

import pytest

from src.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from src.domain.entities.goal import Goal
from src.domain.entities.task import Task
from src.domain.events.outbox import PlanCompleted
from src.domain.value_objects.lifecycle import FailureKind, Status
from src.domain.value_objects.tasks_vos import TaskResult
from src.domain.policies.retry_policies import RetryPolicy

from src.app.use_cases.advance_plan import advance_plan
from src.app.use_cases.control import finish_review
from src.app.testing.fakes import DummyBehavior


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


async def run_to_completion(env, plan, max_steps=50):
    """Drive the plan through RUNNING. Execution exhausts into the REVIEW gate
    (paused); the human "finish" (finish_review) is what reaches DONE — modeled
    here so tests keep asserting the full RUNNING -> REVIEW -> DONE path."""
    env.seed(plan)
    signal = "continue"
    steps = 0
    while signal in ("continue", "not_ready") and steps < max_steps:
        signal = await advance_plan("p1", *env.args)
        if signal == "not_ready":
            env.clock.advance(120)  # model the worker waiting out the backoff gate
        steps += 1
    if signal == "paused" and env.stored("p1").phase == PlanPhase.REVIEW:
        finish_review("p1", env.uow)  # human closes the post-exec gate
        signal = "done"
    return signal


# ---- happy path ----
def test_single_task_completes(env_factory):
    env = env_factory()
    sig = asyncio.run(run_to_completion(env, make_plan()))
    assert sig == "done"
    final = env.stored("p1")
    assert final.phase == PlanPhase.DONE
    assert final.goals[0].tasks[0].status == Status.DONE
    assert env.runner.calls["g0t0"] == 1
    types = env.outbox_types()
    assert "TaskStarted" in types and "TaskCompleted" in types
    assert "PlanCompleted" in types
    assert env.ws.committed and not env.ws.discarded  # workspace committed on success


def test_multiple_tasks_and_goals_sequential(env_factory):
    env = env_factory()
    sig = asyncio.run(run_to_completion(env, make_plan(tasks_per_goal=2, n_goals=2)))
    assert sig == "done"
    final = env.stored("p1")
    assert all(t.status == Status.DONE for g in final.goals for t in g.tasks)
    assert env.outbox_types().count("TaskCompleted") == 4
    assert env.outbox_types().count("GoalCompleted") == 2


# ---- retry path ----
def test_retry_then_succeed(env_factory):
    env = env_factory({"g0t0": DummyBehavior(fail_times=2, output="recovered")})
    sig = asyncio.run(run_to_completion(env, make_plan()))
    assert sig == "done"
    final = env.stored("p1")
    assert final.goals[0].tasks[0].status == Status.DONE
    assert final.goals[0].tasks[0].attempt == 3  # 2 fails + 1 success
    assert env.runner.calls["g0t0"] == 3
    assert env.outbox_types().count("TaskRequeued") == 2  # two requeues
    assert (
        len(env.ws.discarded) == 2 and len(env.ws.committed) == 1
    )  # 2 failed runs discarded, 1 committed


def test_permanent_failure_halts_plan(env_factory):
    env = env_factory({"g0t0": DummyBehavior(always_fail=True, fail_reason="boom")})
    sig = asyncio.run(run_to_completion(env, make_plan(retry_max=3)))
    assert sig == "failed"
    final = env.stored("p1")
    assert final.phase == PlanPhase.FAILED
    assert final.goals[0].tasks[0].status == Status.FAILED
    # attempts: tries until exhausted (max_attempts=3) then terminal
    assert env.runner.calls["g0t0"] == 3
    types = env.outbox_types()
    assert "TaskFailedEvent" in types and "PlanFailed" in types


def test_non_retryable_fails_immediately(env_factory):
    env = env_factory(
        {
            "g0t0": DummyBehavior(
                always_fail=True,
                fail_reason="key rejected",
                fail_kind=FailureKind.AUTH_ERROR,
            )
        }
    )
    sig = asyncio.run(run_to_completion(env, make_plan(retry_max=5)))
    assert sig == "failed"
    assert env.runner.calls["g0t0"] == 1  # non-retryable -> no retries despite max=5


# ---- idempotency / crash recovery ----
def test_check_before_act_skips_completed_task(env_factory):
    """Simulate a crash after the agent ran but before txn2: the task is RUNNING
    with a result already set. advance_plan must finalize WITHOUT calling the agent."""
    env = env_factory()
    plan = make_plan()
    plan.goals[0].tasks[0].status = Status.RUNNING
    plan.goals[0].tasks[0].result = TaskResult.success("already happened")
    plan.goals[0].status = Status.RUNNING
    sig = asyncio.run(run_to_completion(env, plan))
    assert sig == "done"
    assert "g0t0" not in env.runner.calls  # agent NOT re-invoked
    assert env.stored("p1").goals[0].tasks[0].status == Status.DONE


# ---- transactional outbox: rollback discards staged events ----
def test_outbox_rolls_back_on_failed_transaction(env_factory):
    from src.domain.errors.tasks_errors import StaleVersionError

    env = env_factory()
    env.seed(make_plan())
    # force a failure inside the with-block; assert no events leak
    try:
        with env.uow:
            env.uow.outbox.add(PlanCompleted(plan_id="p1"))  # staged
            raise StaleVersionError("p1", 0, 1)
    except StaleVersionError:
        pass
    assert env.outbox_types() == []  # staged event was rolled back, not committed


# ---- agent events flow to the sink, tagged by attempt ----
def test_agent_events_streamed_and_tagged(env_factory):
    env = env_factory({"g0t0": DummyBehavior(emit_events=3)})
    asyncio.run(run_to_completion(env, make_plan()))
    assert len(env.sink.events) == 3
    assert all(e.task_id == "g0t0" for e in env.sink.events)
    assert [e.seq for e in env.sink.events] == [0, 1, 2]


# ---- missing agent fails fast (reactive safety net) ----
def test_missing_agent_raises_before_running(env_factory):
    from src.domain.errors.agent_errors import AgentNotFoundError

    env = env_factory()
    plan = make_plan()
    plan.goals[0].tasks[0].agent_id = "ghost"  # not in registry
    env.seed(plan)
    with pytest.raises(AgentNotFoundError):
        asyncio.run(advance_plan("p1", *env.args))
    assert "g0t0" not in env.runner.calls  # never ran
