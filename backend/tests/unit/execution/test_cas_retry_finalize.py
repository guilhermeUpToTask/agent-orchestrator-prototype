"""CAS-retry-safe finalize (domain unfreeze #13 / Phase 4): the plan.version
hard-equality check that used to gate `_reserve_candidate` is gone — task
identity (status/revision/attempt) is the real fencing token. A concurrent
GOAL's write landing in the narrow get()-to-save() window now transparently
retries instead of either (a) spuriously abandoning valid work (the bug
before this phase, when ANY other goal bumping the version made
`_reserve_candidate` return False) or (b) crashing with an uncaught
StaleVersionError."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from src.app.handlers.execution_handler import ExecutionHandler
from src.app.testing.fakes import (
    CollectingEventSink,
    FakeClock,
    InMemoryAgentRepository,
    InMemoryOutbox,
    InMemoryPlanRepository,
    InMemoryUnitOfWork,
)
from src.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from src.domain.entities.agent_spec import AgentSpec
from src.domain.entities.goal import Goal
from src.domain.entities.planning_artifacts import Cycle, PlanStatus
from src.domain.entities.task import Task
from src.domain.errors.tasks_errors import StaleVersionError
from src.domain.value_objects.lifecycle import Status

NOW = datetime(2026, 7, 22, tzinfo=timezone.utc)


def _agent() -> AgentSpec:
    from src.domain.policies.retry_policies import RetryPolicy

    return AgentSpec(
        id="agent-1",
        name="agent",
        role="agent",
        model_role="agent",
        instructions="",
        default_retry=RetryPolicy(),
    )


def _environment(task: Task):
    goal = Goal(id="goal-1", name="goal", position=0, description="goal", tasks=[task])
    cycle = Cycle(
        id="cycle-1", intent_proposal_id="intent-1", draft_id="draft-1", goals=[goal], started_at=NOW
    )
    plan = Plan(
        id="plan-1",
        project_id="project-1",
        brief="ship safely",
        phase=PlanPhase.RUNNING,
        status=PlanStatus.RUNNING,
        cycles=[cycle],
    )
    clock = FakeClock(NOW)
    plans = InMemoryPlanRepository(clock)
    plans.add(plan)
    uow = InMemoryUnitOfWork(plans, InMemoryOutbox())
    agents = InMemoryAgentRepository([_agent()], default_id="agent-1")
    return plan, plans, uow, agents, clock


class PausableRunner:
    """Blocks inside run() until released, so a test can interject a
    concurrent write between task-start (txn1) and finalize (txn2) — the
    exact window Phase 4's retry-safety targets."""

    def __init__(self) -> None:
        self.can_proceed = asyncio.Event()
        self.entered = asyncio.Event()

    async def run(self, task, spec, *, idempotency_key, event_sink, workspace):
        from src.domain.value_objects.tasks_vos import TaskResult

        self.entered.set()
        await self.can_proceed.wait()
        return TaskResult.success("done")


class NoOpWorkspaceHandle:
    path = "/tmp"
    base_ref = None


class NoOpWorkspace:
    async def begin(self, *args, **kwargs):
        return NoOpWorkspaceHandle()

    async def snapshot(self, handle):
        return "candidate"

    async def checkpoint(self, handle):
        return "checkpoint"

    async def merge_goal(self, plan_id, cycle_id, goal_id):
        return "goal-commit"

    async def commit(self, handle):
        pass

    async def discard(self, handle):
        pass


def test_run_with_cas_retry_recovers_from_transient_conflicts():
    """Isolated proof of the retry primitive itself: body() fails twice with
    StaleVersionError, succeeds on the third attempt -- the retry loop must
    return that success, not the earlier failures."""
    calls = {"count": 0}

    def body() -> str:
        calls["count"] += 1
        if calls["count"] < 3:
            raise StaleVersionError("plan-1", 1, 2)
        return "ok"

    result = ExecutionHandler._run_with_cas_retry(body, max_attempts=5)

    assert result == "ok"
    assert calls["count"] == 3


def test_run_with_cas_retry_reraises_after_exhausting_attempts():
    def always_stale() -> str:
        raise StaleVersionError("plan-1", 1, 2)

    with pytest.raises(StaleVersionError):
        ExecutionHandler._run_with_cas_retry(always_stale, max_attempts=3)


def test_finalize_succeeds_despite_an_unrelated_concurrent_version_bump() -> None:
    """The scenario Phase 4 fixes: task t1 starts (txn1 commits, agent run
    begins), THEN some OTHER goal's worker bumps plan.version via its own
    unrelated write while t1's agent is still "running", THEN t1's agent
    finishes and finalize (txn2) runs. Before this phase, _reserve_candidate's
    `plan.version == unit.plan_version` check would have made this finalize
    spuriously fail (return False, discard t1's real successful work) even
    though nothing about t1 itself changed — task identity
    (status/revision/attempt) is the correct, and now the only, fencing
    token."""

    async def scenario() -> None:
        task = Task(id="t1", name="t1", position=0, description="", agent_id="agent-1")
        plan, plans, uow, agents, clock = _environment(task)
        workspace = NoOpWorkspace()
        runner = PausableRunner()
        handler = ExecutionHandler(runner, agents, workspace, CollectingEventSink(), clock)

        handle_future = asyncio.ensure_future(handler.handle("plan-1", plan, uow))
        await runner.entered.wait()  # txn1 committed: t1 is RUNNING, agent "running"

        started = plans.get("plan-1")
        assert started.active_cycle.goals[0].tasks[0].status == Status.RUNNING

        # simulate a DIFFERENT goal's worker bumping the version via its own,
        # otherwise unrelated, concurrent write -- while t1's agent is still
        # "in flight" from txn1's point of view.
        concurrent_write = plans.get("plan-1")
        concurrent_write.bump_version()
        plans.save(concurrent_write)

        runner.can_proceed.set()
        signal = await handle_future

        assert signal.value == "continue"
        finished = plans.get("plan-1")
        assert finished.active_cycle.goals[0].tasks[0].status == Status.DONE

    asyncio.run(scenario())
