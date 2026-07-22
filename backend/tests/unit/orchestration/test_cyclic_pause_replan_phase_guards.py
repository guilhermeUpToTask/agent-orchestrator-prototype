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
