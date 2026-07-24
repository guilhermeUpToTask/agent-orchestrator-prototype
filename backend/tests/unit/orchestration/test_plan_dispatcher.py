from __future__ import annotations

import asyncio

from src.app.handlers.base import Signal
from src.app.ports import UnitOfWork
from src.app.use_cases.advance_plan import PlanDispatcher
from src.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from src.domain.entities.goal import Goal
from src.domain.entities.planning_artifacts import (
    Cycle,
    IntentProposal,
    PlanStatus,
    ProposalKind,
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


def test_enriched_ready_goal_no_longer_dispatched_by_plan_level_tick(env_factory) -> None:
    """Domain unfreeze #14 (symmetric per-goal leases): the plan-level tick
    stops dispatching execution entirely for a cyclic plan -- an
    enriched-and-ready goal (nothing needs enrichment, not all goals
    terminal) is left for a goal-lease worker (claim_ready_goal / drive_goal)
    to pick up. Before #13 this fell back to ExecutionHandler.handle() and
    ran the task directly here; that fallback is gone."""
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

    assert signal == Signal.NOT_READY
    assert planning.calls == []
    assert env.runner.calls == {}


def test_approved_replan_intent_routes_to_architecture_not_the_source_cycle(env_factory) -> None:
    """Real bug found via a live walkthrough (predates domain unfreeze #14 --
    this routing existed unchanged since unfreeze #13, just never exercised
    by a real reasoner replan on a plan whose source cycle was still active,
    since prior walkthroughs used the stub reasoner via direct CycleDraft
    PUT). A REPLAN's SOURCE cycle stays `active_cycle` for the entire
    drafting window (source-preserving replan: it is superseded only when
    the replacement cycle activates) -- so `active_cycle is not None` being
    checked before "does this plan have an approved intent still waiting on
    architect_cycle" meant an approved replan intent could NEVER reach
    architect_cycle: the dispatcher (and PlanningHandler.handle's own
    routing) kept driving the source cycle's already-enriched goals instead,
    forever, even including re-selecting and re-blocking a goal that had
    already failed there. The approved-intent-needs-architecture check must
    win regardless of active_cycle."""
    env = env_factory()
    planning = RecordingPlanningHandler()
    # source cycle still has a goal with a FAILED task -- exactly the state
    # that used to keep getting re-selected/re-blocked instead of the
    # dispatcher ever reaching architect_cycle for the replacement.
    failed_task = Task(id="t1", name="t1", position=0, description="", agent_id="a1")
    failed_task.status = Status.FAILED
    plan = _cyclic_plan(
        [Goal(id="goal-1", name="Goal 1", position=0, description="source", tasks=[failed_task])],
        env.clock.now(),
    )
    plan.intent_proposal = IntentProposal(
        id="intent-2",
        kind=ProposalKind.REPLAN,
        base_plan_version=plan.version,
        source_cycle_id="cycle-1",
        objective="replan objective",
        approved_at=env.clock.now(),
    )
    env.seed(plan)

    signal = asyncio.run(_dispatcher(env, planning).advance(plan.id, env.uow))

    assert signal == Signal.CONTINUE
    assert planning.calls == [(plan.id, plan.id)]  # routed to architect_cycle, not enrich/execution
    assert env.runner.calls == {}


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
