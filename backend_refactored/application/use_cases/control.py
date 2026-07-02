"""control — pause / resume a plan, and resume from the review gate.

Pausing/resuming is how the human-in-the-loop review gate works: a plan in
AWAITING_REVIEW is resumed (advanced to EXECUTING) once the human is done editing.
All version-CAS guarded.
"""
from __future__ import annotations

from domain.aggregates.planner_orchestrator import PlanPhase
from domain.errors.planning_errors import InvalidEditError

from application.ports import UnitOfWork


def resume_from_review(plan_id: str, uow: UnitOfWork) -> None:
    """Advance a plan waiting at the review gate into execution."""
    with uow:
        plan = uow.plans.get(plan_id)
        if plan.phase != PlanPhase.AWAITING_REVIEW:
            raise InvalidEditError(
                f"plan '{plan_id}' is not awaiting review (phase: {plan.phase.value})")
        plan.advance_phase(PlanPhase.EXECUTING)
        plan.bump_version()
        uow.plans.save(plan)
