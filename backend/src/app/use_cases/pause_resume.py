"""Manual availability controls and targeted retry."""

from __future__ import annotations

from src.domain.errors.planning_errors import InvalidEditError
from src.domain.repositories.agent_repo import AgentRepository
from src.domain.services.agent_role_resolution import resolve_task_role_agents
from src.domain.events.outbox import (
    BlockResolved,
    PauseRequested,
    PlanPaused,
    PlanResumed,
    TaskRetried,
)

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
        block = plan.block if plan.block is not None and plan.block.active else None
        resolution = plan.retry_task(goal_id, task_id, clock.now())
        if resolution == "wait_and_retry":
            circuit_ref = next(
                (
                    ref.removeprefix("runtime-circuit://")
                    for ref in (block.evidence_refs if block is not None else [])
                    if ref.startswith("runtime-circuit://")
                ),
                None,
            )
            if circuit_ref is None:
                raise InvalidEditError("provider retry is missing its runtime circuit reference")
            try:
                runtime, provider_id, model_id = circuit_ref.split("/", 2)
            except ValueError as exc:
                raise InvalidEditError(
                    "provider retry has an invalid runtime circuit reference"
                ) from exc
            uow.executions.clear_runtime_circuit(runtime, provider_id, model_id)
        plan.bump_version()
        task = plan._task(plan._goal(goal_id), task_id)
        if resolution != "edit_task":
            uow.outbox.add(
                TaskRetried(
                    plan_id=plan_id,
                    goal_id=goal_id,
                    task_id=task_id,
                    retry_cycle=task.retry_cycle,
                    next_attempt_number=task.attempt + 1,
                )
            )
        if block is not None and resolution is not None:
            uow.outbox.add(BlockResolved(plan_id=plan_id, block_id=block.id, resolution=resolution))
        uow.plans.save(plan)


def retry_planning_stage(
    plan_id: str,
    uow: UnitOfWork,
    clock: Clock,
    agents: AgentRepository,
) -> None:
    """Retry a reasoner stage or rebind frozen tasks from the repaired registry."""
    with uow:
        plan = uow.plans.get(plan_id)
        if plan.block is None or not plan.block.active:
            raise InvalidEditError("plan has no active block")
        if plan.block.kind == "reasoner_failure":
            block_id = plan.block.id
            plan.retry_planning_stage(clock.now())
            plan.bump_version()
            uow.outbox.add(
                BlockResolved(
                    plan_id=plan_id,
                    block_id=block_id,
                    resolution="retry_stage",
                )
            )
            uow.plans.save(plan)
            return
        if plan.block.kind != "agent_capability" or plan.block.goal_id is None:
            raise InvalidEditError("plan is not blocked on a retryable planning stage")
        block_id = plan.block.id
        goal_id = plan.block.goal_id
        version = plan.version
        requirements = {
            task.id: list(task.required_capabilities) for task in plan._goal(goal_id).tasks
        }

    role_agent_ids_by_task = {
        task_id: resolve_task_role_agents(required, agents)
        for task_id, required in requirements.items()
    }

    with uow:
        plan = uow.plans.get(plan_id)
        if (
            plan.version != version
            or plan.block is None
            or not plan.block.active
            or plan.block.id != block_id
        ):
            raise InvalidEditError("plan changed while agent bindings were being resolved")
        plan.retry_agent_binding(goal_id, role_agent_ids_by_task, clock.now())
        plan.bump_version()
        uow.outbox.add(
            BlockResolved(
                plan_id=plan_id,
                block_id=block_id,
                resolution="retry_stage",
            )
        )
        uow.plans.save(plan)
