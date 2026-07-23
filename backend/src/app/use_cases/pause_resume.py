"""Manual availability controls and targeted retry."""

from __future__ import annotations

from src.domain.aggregates.planner_orchestrator import Plan
from src.domain.entities.planning_artifacts import PlanBlock
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
        # Domain unfreeze #13: the block about to be resolved by plan.retry_task
        # below may be this goal's own entry in goal_blocks (a cyclic plan) or
        # the legacy plan-wide scalar -- never both. Grabbed here (before the
        # call mutates/resolves it) so its evidence_refs/id are still readable
        # for the wait_and_retry circuit lookup and the BlockResolved event.
        goal_block = plan.goal_blocks.get(goal_id) if plan.active_cycle is not None else None
        block = (
            goal_block
            if goal_block is not None and goal_block.active
            else (plan.block if plan.block is not None and plan.block.active else None)
        )
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


def _resolve_retryable_block(plan: Plan, goal_id: str | None) -> tuple[PlanBlock, str | None]:
    """The block `retry_planning_stage` should act on, and the goal_id (if
    any) it belongs to. Domain unfreeze #13: `reasoner_failure` blocks are
    always plan-wide (no goal_id) and live on the legacy scalar `plan.block`.
    `agent_capability` blocks from goal enrichment route into
    `plan.goal_blocks[goal_id]`, and since MULTIPLE goals can be
    independently blocked on it at once, an explicit `goal_id` disambiguates.
    Omitting it works when unambiguous (the legacy scalar, or exactly one
    active per-goal block); with more than one active, this raises rather
    than guessing."""
    if goal_id is not None:
        block = plan.goal_blocks.get(goal_id) if plan.active_cycle is not None else None
        if block is not None and block.active:
            return block, goal_id
        raise InvalidEditError(f"no active block for goal '{goal_id}'")
    if plan.block is not None and plan.block.active:
        return plan.block, plan.block.goal_id
    active = [(gid, block) for gid, block in plan.goal_blocks.items() if block.active]
    if len(active) > 1:
        raise InvalidEditError("multiple goals are blocked; retry-stage requires goal_id")
    if not active:
        raise InvalidEditError("plan has no active block")
    resolved_goal_id, block = active[0]
    return block, resolved_goal_id


def retry_planning_stage(
    plan_id: str,
    uow: UnitOfWork,
    clock: Clock,
    agents: AgentRepository,
    goal_id: str | None = None,
) -> None:
    """Retry a reasoner stage or rebind frozen tasks from the repaired registry."""
    with uow:
        plan = uow.plans.get(plan_id)
        block, target_goal_id = _resolve_retryable_block(plan, goal_id)
        if block.kind == "reasoner_failure":
            block_id = block.id
            plan.retry_planning_stage(clock.now())
            plan.bump_version()
            uow.outbox.add(
                BlockResolved(plan_id=plan_id, block_id=block_id, resolution="retry_stage")
            )
            uow.plans.save(plan)
            return
        if block.kind != "agent_capability" or target_goal_id is None:
            raise InvalidEditError("plan is not blocked on a retryable planning stage")
        block_id = block.id
        version = plan.version
        requirements = {
            task.id: list(task.required_capabilities) for task in plan._goal(target_goal_id).tasks
        }

    role_agent_ids_by_task = {
        task_id: resolve_task_role_agents(required, agents)
        for task_id, required in requirements.items()
    }

    with uow:
        plan = uow.plans.get(plan_id)
        # Re-verify the SAME block (by id), not "which block" again -- that
        # question was already answered in txn1. It may live in either
        # location depending on whether it's per-goal or the legacy scalar
        # (target_goal_id alone doesn't disambiguate that on a second call).
        current = None
        if plan.active_cycle is not None:
            candidate = plan.goal_blocks.get(target_goal_id)
            if candidate is not None and candidate.id == block_id:
                current = candidate
        if current is None and plan.block is not None and plan.block.id == block_id:
            current = plan.block
        if plan.version != version or current is None or not current.active:
            raise InvalidEditError("plan changed while agent bindings were being resolved")
        plan.retry_agent_binding(target_goal_id, role_agent_ids_by_task, clock.now())
        plan.bump_version()
        uow.outbox.add(
            BlockResolved(
                plan_id=plan_id,
                block_id=block_id,
                resolution="retry_stage",
            )
        )
        uow.plans.save(plan)
