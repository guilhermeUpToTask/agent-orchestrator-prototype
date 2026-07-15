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
from src.domain.entities.planning_artifacts import IntentProposal, PlanStatus, ProposalKind
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
