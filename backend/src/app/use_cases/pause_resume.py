"""Manual availability controls and targeted retry."""

from __future__ import annotations

from src.domain.events.outbox import PauseRequested, PlanPaused, PlanResumed, TaskRetried

from src.app.ports import Clock, UnitOfWork


def pause_plan(plan_id: str, uow: UnitOfWork, reason: str | None = None) -> None:
    with uow:
        plan = uow.plans.get(plan_id)
        if plan.paused or plan.pause_requested:
            return
        active_action = bool(uow.executions.list_open_attempts(plan_id))
        plan.request_pause(active_action, reason)
        plan.bump_version()
        if active_action:
            uow.outbox.add(PauseRequested(plan_id=plan_id, reason=reason))
        else:
            uow.outbox.add(PlanPaused(plan_id=plan_id, reason=reason, auto=False))
        uow.plans.save(plan)


def resume_plan(plan_id: str, uow: UnitOfWork) -> None:
    """Remove only the manual pause; retry/backoff state is untouched."""
    with uow:
        plan = uow.plans.get(plan_id)
        plan.resume()
        plan.bump_version()
        uow.outbox.add(PlanResumed(plan_id=plan_id, retried_task_ids=[]))
        uow.plans.save(plan)


def retry_task(
    plan_id: str,
    goal_id: str,
    task_id: str,
    uow: UnitOfWork,
    clock: Clock,
) -> None:
    """Reset policy budget only for the selected failed task."""
    with uow:
        plan = uow.plans.get(plan_id)
        plan.retry_task(goal_id, task_id, clock.now())
        plan.bump_version()
        task = plan._task(plan._goal(goal_id), task_id)
        uow.outbox.add(
            TaskRetried(
                plan_id=plan_id,
                goal_id=goal_id,
                task_id=task_id,
                retry_cycle=task.retry_cycle,
                next_attempt_number=task.attempt + 1,
            )
        )
        uow.plans.save(plan)
