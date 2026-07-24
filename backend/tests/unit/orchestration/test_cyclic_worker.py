from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from src.app.handlers.base import Signal
from src.app.handlers.planning_handler import PlanningHandler
from src.app.testing.fakes import (
    FakeClock,
    InMemoryAgentRepository,
    InMemoryCapabilityRepository,
    InMemoryOutbox,
    InMemoryPlanRepository,
    InMemoryUnitOfWork,
)
from src.app.use_cases.cyclic_planning import activate_cycle
from src.domain.aggregates.planner_orchestrator import Plan
from src.domain.entities.agent_spec import AgentSpec
from src.domain.entities.capability import Capability
from src.domain.entities.goal import Goal
from src.domain.entities.planning_artifacts import (
    Cycle,
    IntentProposal,
    PlanStatus,
    ProposalKind,
)
from src.domain.policies.retry_policies import RetryPolicy
from src.infra.reasoner.stub_reasoner import StubReasoner

NOW = datetime(2026, 7, 14, tzinfo=timezone.utc)


def agent(agent_id: str, capability: str) -> AgentSpec:
    return AgentSpec(
        id=agent_id,
        name=agent_id,
        role=capability,
        model_role="smart",
        instructions="",
        capabilities=[Capability(id=capability, name=capability, description="")],
        default_retry=RetryPolicy(),
    )


def test_worker_architects_then_jit_enriches_with_role_bindings() -> None:
    clock = FakeClock(NOW)
    repo = InMemoryPlanRepository(clock)
    outbox = InMemoryOutbox()
    uow = InMemoryUnitOfWork(repo, outbox)
    agents = InMemoryAgentRepository(
        [
            agent("test-author", "test_authoring"),
            agent("implementer", "implementation"),
        ],
        default_id="implementer",
    )
    handler = PlanningHandler(
        StubReasoner(),
        agents,
        InMemoryCapabilityRepository(),
        clock,
    )
    plan = Plan(
        id="plan-1",
        project_id="project-1",
        brief="ship",
        status=PlanStatus.RUNNING,
        intent_proposal=IntentProposal(
            id="intent-1",
            kind=ProposalKind.INITIAL,
            base_plan_version=0,
            objective="ship",
            approved_at=NOW,
        ),
    )
    repo.add(plan)

    assert asyncio.run(handler.handle(plan.id, plan, uow)) == Signal.PAUSED
    drafted = repo.get(plan.id)
    assert drafted.status == PlanStatus.WAITING
    assert drafted.cycle_draft is not None
    assert drafted.review_gate is not None

    cycle = activate_cycle(
        plan.id,
        drafted.review_gate.id,
        drafted.cycle_draft.revision,
        uow,
        clock,
    )
    active = repo.get(plan.id)
    assert asyncio.run(handler.handle(plan.id, active, uow)) == Signal.CONTINUE

    enriched = repo.get(plan.id)
    goal = enriched.active_cycle.goals[0]  # type: ignore[union-attr]
    assert enriched.active_cycle is not None
    assert enriched.active_cycle.id == cycle.id
    assert goal.contract is not None
    assert goal.contract.frozen_at == NOW
    assert len(goal.tasks) == 1
    assert goal.tasks[0].role_agent_ids == {
        "test_author": "test-author",
        "implementer": "implementer",
    }
    operations = uow.executions.list_planning_operations(plan.id)
    by_purpose = {operation.purpose: operation for operation in operations}
    assert set(by_purpose) == {"cycle_architecture", "goal_contract"}
    assert by_purpose["cycle_architecture"].target_goal_id is None
    assert by_purpose["goal_contract"].target_goal_id == goal.id
    assert all(operation.status.value == "committed" for operation in operations)


def test_enrichment_skips_dependency_blocked_head_goal_for_ready_later_goal() -> None:
    """Goal-parallelism fan-out (ADR-001): a goal stuck behind an unmet
    `depends_on` must not starve a later, independently-ready goal from JIT
    enrichment. Goal A (position 0) depends on a goal that is not DONE; goal B
    (position 1) has no dependencies and is ready right now. The handler must
    pick goal B, not the earliest-position goal overall."""
    clock = FakeClock(NOW)
    repo = InMemoryPlanRepository(clock)
    outbox = InMemoryOutbox()
    uow = InMemoryUnitOfWork(repo, outbox)
    agents = InMemoryAgentRepository(
        [
            agent("test-author", "test_authoring"),
            agent("implementer", "implementation"),
        ],
        default_id="implementer",
    )
    handler = PlanningHandler(
        StubReasoner(),
        agents,
        InMemoryCapabilityRepository(),
        clock,
    )
    goal_a = Goal(
        id="goal-a",
        name="Goal A",
        position=0,
        description="blocked on an unmet dependency",
        depends_on=["some-not-done-goal"],
    )
    goal_b = Goal(
        id="goal-b",
        name="Goal B",
        position=1,
        description="independently ready",
    )
    cycle = Cycle(
        id="cycle-1",
        intent_proposal_id="intent-1",
        draft_id="draft-1",
        goals=[goal_a, goal_b],
        started_at=NOW,
    )
    plan = Plan(
        id="plan-1",
        project_id="project-1",
        brief="ship",
        status=PlanStatus.RUNNING,
        cycles=[cycle],
    )
    repo.add(plan)

    assert asyncio.run(handler.handle(plan.id, plan, uow)) == Signal.CONTINUE

    enriched = repo.get(plan.id)
    assert enriched.active_cycle is not None
    goals_by_id = {g.id: g for g in enriched.active_cycle.goals}
    assert goals_by_id["goal-b"].tasks  # goal B was enriched
    assert not goals_by_id["goal-a"].tasks  # goal A stays untouched (still blocked)
