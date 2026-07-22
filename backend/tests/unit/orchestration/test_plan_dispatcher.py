from __future__ import annotations

import asyncio

from src.app.handlers.base import Signal
from src.app.ports import UnitOfWork
from src.app.use_cases.advance_plan import PlanDispatcher
from src.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from src.domain.entities.goal import Goal
from src.domain.entities.planning_artifacts import (
    Cycle,
    PlanStatus,
    ReviewSubjectType,
)
from src.domain.entities.task import Task
from src.domain.value_objects.lifecycle import Status


class RecordingPlanningHandler:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def handle(self, plan_id: str, plan: Plan, uow: UnitOfWork) -> Signal:
        del uow
        self.calls.append((plan_id, plan.id))
        return Signal.CONTINUE


def _cyclic_plan(goals: list[Goal], started_at) -> Plan:
    return Plan(
        id="p1",
        project_id="project-1",
        brief="ship",
        phase=PlanPhase.RUNNING,
        status=PlanStatus.RUNNING,
        cycles=[
            Cycle(
                id="cycle-1",
                intent_proposal_id="intent-1",
                draft_id="draft-1",
                goals=goals,
                started_at=started_at,
            )
        ],
    )


def _dispatcher(env, planning_handler: RecordingPlanningHandler | None) -> PlanDispatcher:
    return PlanDispatcher(
        env.runner,
        env.agents,
        env.ws,
        env.sink,
        env.clock,
        planning_handler,
    )


def test_ready_later_goal_routes_to_planning_past_blocked_head(env_factory) -> None:
    env = env_factory()
    planning = RecordingPlanningHandler()
    plan = _cyclic_plan(
        [
            Goal(
                id="goal-a",
                name="Goal A",
                position=0,
                description="blocked head goal",
                depends_on=["unfinished-dependency"],
            ),
            Goal(
                id="goal-b",
                name="Goal B",
                position=1,
                description="independently ready goal",
            ),
        ],
        env.clock.now(),
    )
    env.seed(plan)

    signal = asyncio.run(_dispatcher(env, planning).advance(plan.id, env.uow))

    assert signal == Signal.CONTINUE
    assert planning.calls == [(plan.id, plan.id)]
    assert env.runner.calls == {}


def test_enriched_ready_goals_fall_back_to_execution(env_factory) -> None:
    env = env_factory()
    planning = RecordingPlanningHandler()
    task = Task(
        id="task-1",
        name="Task 1",
        position=0,
        description="execute",
        agent_id="a1",
    )
    plan = _cyclic_plan(
        [Goal(id="goal-1", name="Goal 1", position=0, description="ready", tasks=[task])],
        env.clock.now(),
    )
    env.seed(plan)

    signal = asyncio.run(_dispatcher(env, planning).advance(plan.id, env.uow))

    assert signal == Signal.CONTINUE
    assert planning.calls == []
    assert env.runner.calls == {task.id: 1}


def test_missing_planning_handler_pauses_when_ready_goal_needs_enrichment(env_factory) -> None:
    env = env_factory()
    plan = _cyclic_plan(
        [Goal(id="goal-1", name="Goal 1", position=0, description="needs enrichment")],
        env.clock.now(),
    )
    env.seed(plan)

    signal = asyncio.run(_dispatcher(env, None).advance(plan.id, env.uow))

    assert signal == Signal.PAUSED
    assert env.runner.calls == {}


def test_all_terminal_cycle_goals_open_completion_review_gate(env_factory) -> None:
    env = env_factory()
    planning = RecordingPlanningHandler()
    plan = _cyclic_plan(
        [
            Goal(
                id="goal-1",
                name="Goal 1",
                position=0,
                description="complete",
                status=Status.DONE,
            )
        ],
        env.clock.now(),
    )
    env.seed(plan)

    signal = asyncio.run(_dispatcher(env, planning).advance(plan.id, env.uow))

    stored = env.stored(plan.id)
    assert signal == Signal.PAUSED
    assert planning.calls == []
    assert stored.status == PlanStatus.WAITING
    assert stored.review_gate is not None
    assert stored.review_gate.subject_type == ReviewSubjectType.CYCLE_COMPLETION
    assert stored.review_gate.subject_id == "cycle-1"
