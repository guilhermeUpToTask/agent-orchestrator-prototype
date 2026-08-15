"""Reading a plan: the list, and the aggregate DETAIL document.

The detail document is the read model the frontend renders instead of
rebuilding transition rules: status, derived activity, status_reason,
legal_actions, and the sibling facts the aggregate cannot see — the worker
lease and an open provider circuit.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends

from praxis_orchestrator.api.dependencies import get_container
from praxis_orchestrator.app.execution_records import (
    PlanningOperationStatus,
)
from praxis_orchestrator.domain.errors.base import DomainError
from praxis_orchestrator.domain.entities.task import Task
from praxis_orchestrator.infra.container import AppContainer
from praxis_orchestrator.infra.db.unit_of_work import SqliteUnitOfWork


from praxis_orchestrator.api.routers.plans.schemas import (
    ActiveRunResponse,
    PlanDetailResponse,
    ProviderWaitingResponse,
    WorkerLeaseResponse,
    _block_response,
    action_endpoints_for,
)

router = APIRouter(prefix="/plans", tags=["plans"])


@router.get("")
def list_plans(container: AppContainer = Depends(get_container)) -> list[dict]:
    return container.new_unit_of_work().plans.list_summaries()




def _worker_lease(
    plan_id: str,
    current_goal_id: str | None,
    now: datetime,
    uow: SqliteUnitOfWork,
) -> WorkerLeaseResponse | None:
    """The lease actually holding this plan's work, expired or not.

    Goal lease first: execution runs under it, and it is the one that goes stale
    when a goal worker dies mid-attempt. The plan claim is the fallback, since
    planning turns and gates run under that instead.
    """
    holder: tuple[str, datetime] | None = None
    scope: Literal["goal", "plan"] = "goal"
    goal_id = current_goal_id
    if current_goal_id is not None:
        holder = uow.goal_leases.lease_holder(plan_id, current_goal_id)
    if holder is None:
        holder = uow.plans.lease_holder(plan_id)
        scope, goal_id = "plan", None
    if holder is None:
        return None
    worker_id, expires_at = holder
    remaining = (expires_at - now).total_seconds()
    return WorkerLeaseResponse(
        scope=scope,
        goal_id=goal_id,
        worker_id=worker_id,
        expires_at=expires_at.isoformat(),
        expired=remaining <= 0,
        seconds_remaining=int(remaining),
    )


def _provider_waiting(
    current_task: Task | None,
    container: AppContainer,
    uow: SqliteUnitOfWork,
) -> ProviderWaitingResponse | None:
    """The capacity circuit gating this plan's next work, if any.

    Checks the PROVIDER-WIDE circuit first: an account-level limit is shared by
    every model on the key, so it outranks a single model's wait. Best-effort —
    a plan whose binding cannot be resolved simply reports no wait rather than
    failing the read.
    """
    if current_task is None:
        return None
    agent_id = current_task.role_agent_ids.get("implementer", current_task.agent_id)
    try:
        spec = (
            container.agent_repo.get(agent_id)
            if agent_id
            else container.agent_repo.get(container.agent_repo.default_agent_id())
        )
    except DomainError:
        return None
    if not spec.provider_id or not spec.model_id:
        return None
    circuit = uow.executions.get_runtime_circuit(
        spec.runtime_type, spec.provider_id, None
    ) or uow.executions.get_runtime_circuit(spec.runtime_type, spec.provider_id, spec.model_id)
    if circuit is None:
        return None
    return ProviderWaitingResponse(
        provider_id=circuit.provider_id,
        model_id=circuit.model_id,
        runtime=circuit.runtime,
        limit_scope=circuit.limit_scope,
        retry_at=circuit.retry_at.isoformat(),
        since=circuit.opened_at.isoformat(),
        failure_count=circuit.failure_count,
        safe_message=circuit.safe_message,
        needs_attention=circuit.manual_intervention,
    )




@router.get("/{plan_id}", response_model=PlanDetailResponse)
def get_plan(plan_id: str, container: AppContainer = Depends(get_container)) -> PlanDetailResponse:
    uow = container.new_unit_of_work()
    with uow:
        plan = uow.plans.get(plan_id)
        open_attempts = uow.executions.list_open_attempts(plan_id)
        planning_operations = uow.executions.list_planning_operations(plan_id)
    latest = max(open_attempts, key=lambda attempt: attempt.number, default=None)
    cycle = plan.active_cycle
    goals = cycle.goals if cycle is not None else plan.goals
    current_goal = min(
        (goal for goal in goals if not goal.is_terminal),
        key=lambda goal: goal.position,
        default=None,
    )
    current_task = (
        min(
            (task for task in current_goal.tasks if not task.is_terminal),
            key=lambda task: task.position,
            default=None,
        )
        if current_goal is not None
        else None
    )
    # Needs `current_task` (computed above, after the first txn closed), so it
    # takes its own short read transaction.
    with uow:
        provider_waiting = _provider_waiting(current_task, container, uow)
        worker_lease = _worker_lease(
            plan.id,
            current_goal.id if current_goal is not None else None,
            container.clock.now(),
            uow,
        )
    planning_operation = planning_operations[-1] if planning_operations else None
    goal_position = (
        next(
            (
                index
                for index, goal in enumerate(sorted(goals, key=lambda item: item.position), 1)
                if current_goal is not None and goal.id == current_goal.id
            ),
            None,
        )
        if goals
        else None
    )
    planning_progress = None
    if planning_operation is not None and planning_operation.status in {
        PlanningOperationStatus.QUEUED,
        PlanningOperationStatus.STARTED,
        PlanningOperationStatus.BACKING_OFF,
    }:
        if planning_operation.purpose == "goal_contract" and goal_position is not None:
            planning_progress = f"Generating tasks for goal {goal_position} of {len(goals)}"
        elif planning_operation.purpose == "cycle_architecture":
            planning_progress = "Generating the cycle roadmap"
        else:
            planning_progress = "Analyzing the brief"
    return PlanDetailResponse(
        id=plan.id,
        project_id=plan.project_id,
        brief=plan.brief,
        version=plan.version,
        status=plan.status.value,
        status_reason=plan.status_reason,
        activity=plan.activity,
        current_goal_id=current_goal.id if current_goal is not None else None,
        current_task_id=current_task.id if current_task is not None else None,
        tdd_stage=current_task.tdd_stage if current_task is not None else None,
        legal_actions=plan.legal_actions,
        action_endpoints=action_endpoints_for(plan),
        pause_requested=plan.pause_requested,
        paused=plan.paused,
        paused_reason=plan.paused_reason,
        worker_lease=worker_lease,
        provider_waiting=provider_waiting,
        active_run=(
            ActiveRunResponse(
                run_id=latest.run_id,
                attempt_id=latest.id,
                attempt_number=latest.number,
                goal_id=latest.goal_id,
                task_id=latest.task_id,
                started_at=latest.started_at.isoformat(),
            )
            if latest is not None
            else None
        ),
        planning_operation=(
            {
                "id": planning_operation.id,
                "purpose": planning_operation.purpose,
                "target_goal_id": planning_operation.target_goal_id,
                "status": planning_operation.status.value,
                "updated_at": planning_operation.updated_at.isoformat(),
                "retry_at": (
                    planning_operation.retry_at.isoformat()
                    if planning_operation.retry_at is not None
                    else None
                ),
                "safe_message": planning_operation.safe_message,
            }
            if planning_operation is not None
            else None
        ),
        planning_progress=planning_progress,
        active_cycle=cycle,
        pending_gate=(
            plan.review_gate
            if plan.review_gate is not None and plan.review_gate.unresolved
            else None
        ),
        block=(
            _block_response(plan.block)
            if plan.block is not None and plan.block.active
            else None
        ),
        goal_blocks={
            goal_id: _block_response(block)
            for goal_id, block in plan.goal_blocks.items()
            if block.active
        },
        goals=goals,
        cycles=plan.cycles,
        intent_proposal=plan.intent_proposal,
        cycle_draft=plan.cycle_draft,
        legacy_phase=plan.legacy_phase,
        phase=plan.phase.value,
        iteration=plan.iteration,
    )


