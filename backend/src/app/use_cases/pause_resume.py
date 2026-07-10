"""pause/resume — the human pause gate and the manual retry (un-freeze #3).

Pause arms Plan.paused, an availability flag on the claim predicate (the same
durable-gate pattern as the planning backoff): the worker stops claiming the
plan at the next unit boundary — an in-flight attempt still finalizes — and
goals/tasks become manually editable while the gate holds. Resume clears the
gate and requeues failed work (decision #17's manual retry): every FAILED task
in a non-terminal goal returns to PENDING with a fresh attempt budget.

The auto-pause twin lives in the ExecutionHandler: a terminal task failure
pauses the plan in the finalize transaction and emits PlanPaused(auto=True).
"""
from __future__ import annotations

from src.domain.events.outbox import PlanPaused, PlanResumed

from src.app.ports import UnitOfWork


def pause_plan(plan_id: str, uow: UnitOfWork, reason: str | None = None) -> None:
    """Human pause command. Idempotent: pausing an already-paused plan is a
    no-op (no state bump, no event)."""
    with uow:
        plan = uow.plans.get(plan_id)
        if plan.paused:
            return
        plan.pause(reason)
        plan.bump_version()
        uow.outbox.add(PlanPaused(plan_id=plan_id, reason=reason, auto=False))
        uow.plans.save(plan)


def resume_plan(plan_id: str, uow: UnitOfWork) -> None:
    """Human resume command = the manual retry. Raises InvalidTransitionError
    (422) when the plan is not paused."""
    with uow:
        plan = uow.plans.get(plan_id)
        retried = plan.resume()
        plan.bump_version()
        uow.outbox.add(PlanResumed(plan_id=plan_id, retried_task_ids=retried))
        uow.plans.save(plan)
