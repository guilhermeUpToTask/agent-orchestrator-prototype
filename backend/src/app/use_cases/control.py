"""control — the human commands that drive the two gates.

  AWAITING_REVIEW (pre-execution):  approve            -> RUNNING
  REVIEW          (post-execution): finish             -> DONE (PlanCompleted)
                                    replan next phase  -> REPLANNING

All version-CAS guarded. The phase guards live on the aggregate (guarded named
transitions), so a command against the wrong phase raises InvalidTransitionError.
"""
from __future__ import annotations

from src.domain.aggregates.planner_orchestrator import PlanPhase
from src.domain.events.outbox import PhaseAdvanced, PlanCompleted

from src.app.ports import UnitOfWork
from src.app.use_cases.request_replan import request_replan


def resume_from_review(plan_id: str, uow: UnitOfWork) -> None:
    """Human approval at the pre-execution gate: advance into execution."""
    with uow:
        plan = uow.plans.get(plan_id)
        plan.approve()
        plan.bump_version()
        uow.outbox.add(
            PhaseAdvanced(
                plan_id=plan_id,
                from_phase=PlanPhase.AWAITING_REVIEW.value,
                to_phase=PlanPhase.RUNNING.value,
            )
        )
        uow.plans.save(plan)


def reopen_discovery(plan_id: str, uow: UnitOfWork) -> None:
    """Human "request changes" at the pre-execution gate: AWAITING_REVIEW ->
    DISCOVERY. Re-opens the planning conversation; the next commit REPLACES the
    un-executed roadmap (set_iteration_goals keeps only terminal history)."""
    with uow:
        plan = uow.plans.get(plan_id)
        plan.reopen_discovery()
        plan.bump_version()
        uow.outbox.add(
            PhaseAdvanced(
                plan_id=plan_id,
                from_phase=PlanPhase.AWAITING_REVIEW.value,
                to_phase=PlanPhase.DISCOVERY.value,
            )
        )
        uow.plans.save(plan)


def finish_review(plan_id: str, uow: UnitOfWork) -> None:
    """Human "finish" at the post-execution gate: REVIEW -> DONE. This is the ONLY
    place a plan reaches DONE, and where PlanCompleted is emitted."""
    with uow:
        plan = uow.plans.get(plan_id)
        plan.finish_review()
        plan.bump_version()
        uow.outbox.add(PlanCompleted(plan_id=plan_id))
        uow.plans.save(plan)


def review_replan(plan_id: str, uow: UnitOfWork) -> None:
    """Human "replan next phase" at the post-execution gate: REVIEW -> REPLANNING.
    Same state machinery as the mid-RUNNING request_replan (the aggregate guard
    accepts both entry points)."""
    request_replan(plan_id, uow)
