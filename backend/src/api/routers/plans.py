"""
/api/plans — the plan lifecycle: create, inspect, edit, and the human commands
that drive the two gates and the replan loop. Routes map 1:1 onto use cases;
errors bubble to the global mapping layer.
"""
from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel

from src.api.dependencies import get_container
from src.app.use_cases import control
from src.app.use_cases.apply_edit import (
    AddTask,
    Edit,
    EditTaskRequirements,
    RebindTaskAgent,
    RemoveTask,
    ReorderTasks,
    apply_edit,
)
from src.app.use_cases.conversation import discovery_message, replanning_message
from src.app.use_cases.create_plan import create_plan
from src.app.use_cases.request_replan import request_replan
from src.domain.entities.task import Task
from src.domain.errors.planning_errors import InvalidEditError
from src.domain.factories.identity import new_id
from src.infra.container import AppContainer

router = APIRouter(prefix="/plans", tags=["plans"])


# ---- DTOs ----
class CreatePlanRequest(BaseModel):
    brief: str


class PlanCreatedResponse(BaseModel):
    plan_id: str


class MessageRequest(BaseModel):
    message: str


class NewTaskBody(BaseModel):
    name: str
    description: str = ""
    required_capabilities: list[str] = []


class EditRequest(BaseModel):
    """One structural edit. `type` selects the operation; the other fields are
    per-type (validated in _to_edit so a bad combination 422s, not 500s)."""

    type: Literal[
        "add_task",
        "remove_task",
        "reorder_tasks",
        "edit_task_requirements",
        "rebind_task_agent",
    ]
    goal_id: str
    task_id: str | None = None
    task: NewTaskBody | None = None
    ordered_task_ids: list[str] | None = None
    required_capabilities: list[str] | None = None
    agent_id: str | None = None


def _require(value: Any, field: str, edit_type: str) -> Any:
    if value is None:
        raise InvalidEditError(f"edit '{edit_type}' requires '{field}'")
    return value


def _to_edit(body: EditRequest) -> Edit:
    if body.type == "add_task":
        task = _require(body.task, "task", body.type)
        return AddTask(
            goal_id=body.goal_id,
            task=Task(
                id=new_id(),
                name=task.name,
                position=10**6,  # renumbered by the edit service
                description=task.description,
                required_capabilities=task.required_capabilities,
            ),
        )
    if body.type == "remove_task":
        return RemoveTask(body.goal_id, _require(body.task_id, "task_id", body.type))
    if body.type == "reorder_tasks":
        return ReorderTasks(
            body.goal_id,
            _require(body.ordered_task_ids, "ordered_task_ids", body.type),
        )
    if body.type == "edit_task_requirements":
        return EditTaskRequirements(
            body.goal_id,
            _require(body.task_id, "task_id", body.type),
            _require(body.required_capabilities, "required_capabilities", body.type),
        )
    return RebindTaskAgent(
        body.goal_id,
        _require(body.task_id, "task_id", body.type),
        _require(body.agent_id, "agent_id", body.type),
    )


# ---- routes ----
@router.post("", response_model=PlanCreatedResponse, status_code=201)
def create(
    body: CreatePlanRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    container: AppContainer = Depends(get_container),
) -> PlanCreatedResponse:
    request_id = idempotency_key or str(uuid.uuid4())
    plan_id = create_plan(body.brief, request_id, container.new_unit_of_work())
    return PlanCreatedResponse(plan_id=plan_id)


@router.get("")
def list_plans(container: AppContainer = Depends(get_container)) -> list[dict]:
    return container.new_unit_of_work().plans.list_summaries()


@router.get("/{plan_id}")
def get_plan(plan_id: str, container: AppContainer = Depends(get_container)) -> dict:
    uow = container.new_unit_of_work()
    with uow:
        plan = uow.plans.get(plan_id)
    return plan.model_dump(mode="json")


@router.post("/{plan_id}/edits", status_code=204)
def edit_plan(
    plan_id: str,
    body: EditRequest,
    container: AppContainer = Depends(get_container),
) -> None:
    apply_edit(
        plan_id,
        _to_edit(body),
        container.new_unit_of_work(),
        container.capability_repo,
        container.agent_repo,
    )


@router.post("/{plan_id}/approve", status_code=204)
def approve(plan_id: str, container: AppContainer = Depends(get_container)) -> None:
    """Human approval at the pre-execution gate: AWAITING_REVIEW -> RUNNING."""
    control.resume_from_review(plan_id, container.new_unit_of_work())


@router.post("/{plan_id}/review/finish", status_code=204)
def finish(plan_id: str, container: AppContainer = Depends(get_container)) -> None:
    """Human "finish" at the post-execution gate: REVIEW -> DONE."""
    control.finish_review(plan_id, container.new_unit_of_work())


@router.post("/{plan_id}/review/replan", status_code=204)
def replan_from_review(
    plan_id: str, container: AppContainer = Depends(get_container)
) -> None:
    """Human "replan next phase" at the post-execution gate: REVIEW -> REPLANNING."""
    control.review_replan(plan_id, container.new_unit_of_work())


@router.post("/{plan_id}/replan", status_code=204)
def replan_mid_running(
    plan_id: str, container: AppContainer = Depends(get_container)
) -> None:
    """Chat-triggered mid-RUNNING replan: skip pending work -> REPLANNING."""
    request_replan(plan_id, container.new_unit_of_work())


@router.post("/{plan_id}/discovery/message", status_code=204)
async def discovery(
    plan_id: str,
    body: MessageRequest,
    container: AppContainer = Depends(get_container),
) -> None:
    """One DISCOVERY conversation turn (drafts goals -> ARCHITECTURE)."""
    await discovery_message(
        plan_id, body.message, container.new_unit_of_work(), container.reasoner
    )


@router.post("/{plan_id}/replanning/message", status_code=204)
async def replanning(
    plan_id: str,
    body: MessageRequest,
    container: AppContainer = Depends(get_container),
) -> None:
    """One REPLANNING conversation turn (commits the new goal set ->
    ARCHITECTURE; the iteration increments here)."""
    await replanning_message(
        plan_id, body.message, container.new_unit_of_work(), container.reasoner
    )
