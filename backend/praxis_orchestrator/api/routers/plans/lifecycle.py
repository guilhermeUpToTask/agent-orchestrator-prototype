"""Creating, binding and deleting a plan — the routes that make one exist."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header

from praxis_orchestrator.api.dependencies import get_container
from praxis_orchestrator.app.execution_records import (
    PlanningOperation,
    PlanningOperationStatus,
)
from praxis_orchestrator.app.ports import ChatMessage
from praxis_orchestrator.app.use_cases.conversation import discovery_message, replanning_message
from praxis_orchestrator.app.use_cases.create_plan import open_project_plan
from praxis_orchestrator.app.use_cases.bind_project import bind_legacy_project
from praxis_orchestrator.app.use_cases.delete_plan import delete_plan
from praxis_orchestrator.domain.errors.planning_errors import InvalidEditError
from praxis_orchestrator.infra.container import AppContainer


from praxis_orchestrator.api.routers.plans.schemas import (
    CreatePlanRequest,
    PlanCreatedResponse,
    ProjectBindingRequest,
)

router = APIRouter(prefix="/plans", tags=["plans"])


@router.post("/{plan_id}/project-binding", status_code=204)
def bind_project_route(
    plan_id: str,
    body: ProjectBindingRequest,
    container: AppContainer = Depends(get_container),
) -> None:
    bind_legacy_project(
        plan_id,
        body.project_id,
        container.new_unit_of_work(),
        container.project_repo,
        container.clock,
    )


@router.post("", response_model=PlanCreatedResponse, status_code=201)
async def create(
    body: CreatePlanRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    container: AppContainer = Depends(get_container),
) -> PlanCreatedResponse:
    request_id = idempotency_key or str(uuid.uuid4())
    container.project_repo.get(body.project_id)
    opened = open_project_plan(
        body.brief,
        body.project_id,
        request_id,
        container.new_unit_of_work(),
        retry_policy=container.default_retry_policy,
    )
    if opened.request_replayed:
        with container.new_unit_of_work() as uow:
            active = uow.executions.find_active_planning_operation(
                opened.plan_id, "intent_discovery"
            ) or uow.executions.find_active_planning_operation(opened.plan_id, "replan_discovery")
            operations = uow.executions.list_planning_operations(opened.plan_id)
            operation = active or (operations[-1] if operations else None)
        return PlanCreatedResponse(
            plan_id=opened.plan_id,
            created=False,
            opened_existing=True,
            brief_preserved=True,
            discovery_operation_id=(operation.id if operation else None),
            discovery_status=(operation.status.value if operation else None),
            discovery_reply=None,
            discovery_error=None,
        )

    with container.new_unit_of_work() as uow:
        plan = uow.plans.get(opened.plan_id)
        replan = plan.active_cycle is not None
    try:
        result = await (
            replanning_message(
                opened.plan_id,
                body.brief,
                container.new_unit_of_work(),
                container.reasoner,
                container.chat_store,
                container.clock,
            )
            if replan
            else discovery_message(
                opened.plan_id,
                body.brief,
                container.new_unit_of_work(),
                container.reasoner,
                container.chat_store,
                container.clock,
            )
        )
    except InvalidEditError as exc:
        now = container.clock.now()
        operation = PlanningOperation(
            id=str(uuid.uuid4()),
            plan_id=opened.plan_id,
            purpose=("replan_discovery" if replan else "intent_discovery"),
            status=PlanningOperationStatus.FAILED,
            created_at=now,
            updated_at=now,
            started_at=now,
            completed_at=now,
            last_liveness_at=now,
            failure_kind="planning_conflict",
            safe_message=str(exc)[:500],
        )
        with container.new_unit_of_work() as uow:
            uow.executions.add_planning_operation(operation)
        container.chat_store.append(
            opened.plan_id,
            ChatMessage(
                role="user",
                content=body.brief,
                created_at=now,
                meta={
                    "submitted_brief": True,
                    "applied": False,
                    "planning_operation_id": operation.id,
                    "planning_status": operation.status.value,
                },
            ),
        )
        return PlanCreatedResponse(
            plan_id=opened.plan_id,
            created=opened.created,
            opened_existing=not opened.created,
            brief_preserved=True,
            discovery_operation_id=operation.id,
            discovery_status=operation.status.value,
            discovery_reply=None,
            discovery_error=operation.safe_message,
        )

    return PlanCreatedResponse(
        plan_id=opened.plan_id,
        created=opened.created,
        opened_existing=not opened.created,
        brief_preserved=True,
        discovery_operation_id=result.operation_id,
        discovery_status=result.operation_status.value,
        discovery_reply=result.reply,
        discovery_error=result.error,
    )




@router.delete("/{plan_id}", status_code=204)
def delete_plan_route(plan_id: str, container: AppContainer = Depends(get_container)) -> None:
    """Dispose of a plan and everything produced under it.

    404 when it does not exist, 409 (`PLAN_BUSY`) while a worker holds a live
    lease. Irreversible: cycles, attempts, evidence, chat and telemetry go with
    it, so export the run evidence first if it is worth keeping.
    """
    delete_plan(plan_id, container.new_unit_of_work())


