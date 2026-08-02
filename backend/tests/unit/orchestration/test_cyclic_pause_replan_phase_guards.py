from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_orchestrator.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from agent_orchestrator.domain.entities.planning_artifacts import (
    Cycle,
    IntentProposal,
    PlanStatus,
    ProposalKind,
    ReviewGate,
    ReviewSubjectType,
)
from agent_orchestrator.domain.errors.tasks_errors import InvalidTransitionError

NOW = datetime(2026, 7, 14, tzinfo=timezone.utc)


def cyclic_plan_with_replanning_legacy_phase() -> Plan:
    plan = Plan(id="plan-1", project_id="project-1", brief="brief")
    plan.cycles = [
        Cycle(
            id="cycle-1",
            intent_proposal_id="intent-1",
            draft_id="draft-1",
            started_at=NOW,
            goals=[],
        )
    ]
    plan._set_phase(PlanPhase.REPLANNING)
    plan.status = PlanStatus.RUNNING
    return plan


def test_cyclic_running_plan_can_pause_with_replanning_legacy_phase() -> None:
    plan = cyclic_plan_with_replanning_legacy_phase()

    plan.request_pause(active_action=False)

    assert plan.paused
    assert plan.status == PlanStatus.PAUSED


def test_cyclic_plan_can_begin_replanning_with_replanning_legacy_phase() -> None:
    plan = cyclic_plan_with_replanning_legacy_phase()

    plan.begin_replanning()

    assert plan.phase == PlanPhase.REPLANNING


def test_legacy_plan_cannot_begin_replanning_from_replanning() -> None:
    plan = Plan(id="plan-1", brief="brief", phase=PlanPhase.REPLANNING)

    with pytest.raises(InvalidTransitionError):
        plan.begin_replanning()


def test_legacy_plan_cannot_pause_from_non_claimable_phase() -> None:
    plan = Plan(id="plan-1", brief="brief", phase=PlanPhase.REPLANNING)

    with pytest.raises(InvalidTransitionError):
        plan.pause()


def test_a_cyclic_plan_awaiting_architecture_can_pause() -> None:
    """Unfreeze #19. Between an approved intent and an activated cycle a plan is
    fully cyclic and has NO cycle yet, so `active_cycle is not None` was the
    wrong test for "is this cyclic": the guard fell through to the LEGACY phase,
    which cyclic planning never advances past `discovery`, and refused.

    The plan is RUNNING and `_CLAIM_SQL` considers it claimable, so a worker can
    pick it up — while `legal_actions` advertised `pause` and the endpoint
    answered 422. Found by the Phase 4 advertised-action contract test, in the
    exact window an operator most wants to pause: waiting on the planner.
    """
    plan = Plan(id="plan-1", brief="brief", project_id="project-1")
    plan.propose_intent(
        IntentProposal(
            id="intent-1",
            revision=1,
            kind=ProposalKind.INITIAL,
            base_plan_version=0,
            objective="ship it",
        ),
        ReviewGate(
            id="gate-1",
            subject_type=ReviewSubjectType.INTENT,
            subject_id="intent-1",
            subject_revision=1,
            allowed_decisions=["approve"],
            continuation="approve the intent",
            opened_at=NOW,
        ),
    )
    plan.approve_intent("gate-1", 1, NOW)
    plan.status = PlanStatus.RUNNING
    assert plan.active_cycle is None, "the window this test exists for"

    plan.request_pause(active_action=False)

    assert plan.paused
    assert plan.status == PlanStatus.PAUSED


def test_a_legacy_plan_is_still_judged_by_its_phase() -> None:
    """The other half of unfreeze #19: a legacy row has no cyclic artifact, so
    the phase check still governs it and this must still refuse."""
    plan = Plan(id="plan-1", brief="brief", phase=PlanPhase.REPLANNING)
    plan.status = PlanStatus.RUNNING

    with pytest.raises(InvalidTransitionError):
        plan.request_pause(active_action=False)
