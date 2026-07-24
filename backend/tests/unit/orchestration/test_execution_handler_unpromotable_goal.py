"""Regression lock for the unpromotable-goal hot loop.

Navigation's pull-scan returns `(goal, None)` once every task in the earliest
non-terminal goal is terminal and none is FAILED — the "close this goal"
signal. `_reserve_goal_promotion` additionally requires every task be DONE
*with* accepted verification evidence before it will reserve the Git merge. A
DONE task with no evidence (a legacy/replan artifact) used to let that
`TaskFailed` escape `ExecutionHandler.handle()`; the worker loop would then
re-dispatch the same plan/goal forever, re-raising the same exception every
tick. The fix catches it in `handle()` and opens a recoverable
`execution_failure` block instead (`_block_on_unpromotable_goal`)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from src.app.handlers.execution_handler import ExecutionHandler
from src.app.testing.fakes import (
    CollectingEventSink,
    DummyAgentRunner,
    FakeClock,
    InMemoryAgentRepository,
    InMemoryOutbox,
    InMemoryPlanRepository,
    InMemoryUnitOfWork,
    NoOpWorkspace,
)
from src.app.handlers.base import Signal
from src.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from src.domain.entities.goal import Goal
from src.domain.entities.planning_artifacts import Cycle, PlanStatus
from src.domain.entities.task import Task
from src.domain.value_objects.lifecycle import Status
from src.domain.value_objects.tasks_vos import TaskResult

NOW = datetime(2026, 7, 14, tzinfo=timezone.utc)


def test_unpromotable_done_goal_blocks_instead_of_raising() -> None:
    # A DONE task with no verification_evidence: navigation sees an all-terminal,
    # no-failure goal and returns (goal, None) — "close it" — but the task never
    # earned accepted evidence, so promotion cannot legally proceed.
    task = Task(
        id="task-1",
        name="implement feature",
        position=0,
        description="implement feature",
        status=Status.DONE,
        result=TaskResult.success("looked done, never independently verified"),
    )
    goal = Goal(id="goal-1", name="goal", position=0, description="goal", tasks=[task])
    cycle = Cycle(
        id="cycle-1",
        intent_proposal_id="intent-1",
        draft_id="draft-1",
        goals=[goal],
        started_at=NOW,
    )
    plan = Plan(
        id="plan-1",
        project_id="project-1",
        brief="brief",
        phase=PlanPhase.RUNNING,
        status=PlanStatus.RUNNING,
        cycles=[cycle],
    )

    clock = FakeClock(NOW)
    plans = InMemoryPlanRepository(clock)
    plans.add(plan)
    uow = InMemoryUnitOfWork(plans, InMemoryOutbox())
    handler = ExecutionHandler(
        DummyAgentRunner(),
        InMemoryAgentRepository([]),
        NoOpWorkspace(),
        CollectingEventSink(),
        clock,
    )

    signal = asyncio.run(handler.handle(plan.id, plan, uow))

    assert signal == Signal.PAUSED
    blocked = plans.get(plan.id)
    assert blocked.status == PlanStatus.BLOCKED
    # Domain unfreeze #14: a cyclic goal's block routes into goal_blocks, not
    # the legacy scalar `block` (which stays None for cyclic per-goal blocks).
    assert blocked.block is None
    block = blocked.goal_blocks.get("goal-1")
    assert block is not None
    assert block.active
    assert block.kind == "execution_failure"
    assert block.goal_id == "goal-1"
    assert block.task_id == "task-1"
    assert "edit_task" in block.legal_resolutions
    assert "start_replan" in block.legal_resolutions
    # The goal never merges/completes on this path.
    still_open_goal = blocked.active_cycle.goals[0]  # type: ignore[union-attr]
    assert still_open_goal.status != Status.DONE
