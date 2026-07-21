from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from src.domain.entities.planning_artifacts import Cycle, PlanStatus
from src.domain.errors.tasks_errors import InvalidTransitionError

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


def test_paused_status_without_paused_flag_is_not_resumable() -> None:
    # Unfreeze #10: an inconsistent status=PAUSED with paused=False must NOT
    # advertise resume (resume() guards on the paused field and would reject it).
    plan = cyclic_plan_with_replanning_legacy_phase()
    plan.status = PlanStatus.PAUSED
    plan.paused = False
    assert "resume" not in plan.legal_actions


def test_genuinely_paused_cyclic_plan_advertises_resume() -> None:
    plan = cyclic_plan_with_replanning_legacy_phase()
    plan.request_pause(active_action=False)  # genuinely armed manual pause
    assert plan.paused and plan.status == PlanStatus.PAUSED
    assert "resume" in plan.legal_actions


def test_cyclic_begin_replanning_lands_in_waiting_replan_discovery() -> None:
    # Unfreeze #10: begin_replanning on a cyclic plan lands in the coherent
    # WAITING replan_discovery tuple (not status=PAUSED/paused=False), so resume
    # is never advertised and the plan is not worker-claimable.
    plan = cyclic_plan_with_replanning_legacy_phase()
    plan.begin_replanning()
    assert plan.status == PlanStatus.WAITING
    assert plan.paused is False and plan.pause_requested is False
    assert plan.intent_proposal is None and plan.cycle_draft is None and plan.review_gate is None
    assert plan.active_cycle is not None
    assert plan.activity == "replan_discovery"
    assert "resume" not in plan.legal_actions
