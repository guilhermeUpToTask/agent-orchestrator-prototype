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
    RemoveGoal,
    RemoveTask,
    ReorderTasks,
    UpdateGoal,
    UpdateTask,
    apply_edit,
)
from src.app.use_cases.conversation import discovery_message, replanning_message
from src.app.use_cases.create_plan import create_plan
from src.app.use_cases.cyclic_planning import (
    activate_cycle,
    approve_intent,
    cancel_cycle_draft,
    cancel_intent,
    propose_intent,
    record_output_disposition,
    revise_cycle_draft,
    revise_intent,
    submit_cycle_draft as submit_cycle_draft_use_case,
)
from src.app.use_cases.pause_resume import pause_plan, resume_plan, retry_task
from src.app.use_cases.request_replan import request_replan
from src.domain.entities.planning_artifacts import (
    GoalOutline,
    OutputDisposition,
    ProposalKind,
)
from src.domain.entities.task import Task
from src.domain.errors.planning_errors import InvalidEditError
from src.domain.factories.identity import new_id
from src.infra.container import AppContainer

router = APIRouter(prefix="/plans", tags=["plans"])


# ---- DTOs ----
class CreatePlanRequest(BaseModel):
    brief: str
    project_id: str


class PlanCreatedResponse(BaseModel):
    plan_id: str


class ActiveRunResponse(BaseModel):
    run_id: str
    attempt_id: str
    attempt_number: int
    goal_id: str
    task_id: str
    started_at: str


class PlanDetailResponse(BaseModel):
    id: str
    project_id: str | None
    brief: str
    version: int
    status: str
    status_reason: dict[str, str | None]
    activity: str
    current_goal_id: str | None
    current_task_id: str | None
    tdd_stage: str | None
    legal_actions: list[str]
    pause_requested: bool
    paused: bool
    paused_reason: str | None
    active_run: ActiveRunResponse | None
    active_cycle: dict[str, Any] | None
    pending_gate: dict[str, Any] | None
    block: dict[str, Any] | None
    goals: list[dict[str, Any]]
    cycles: list[dict[str, Any]]
    intent_proposal: dict[str, Any] | None
    cycle_draft: dict[str, Any] | None
    legacy_phase: str | None = None
    phase: str | None = None
    iteration: int | None = None


class IntentProposalRequest(BaseModel):
    objective: str
    scope: list[str] = []
    constraints: list[str] = []
    exclusions: list[str] = []
    kind: ProposalKind = ProposalKind.INITIAL
    planner_session_ref: str | None = None


class ReviewDecisionRequest(BaseModel):
    gate_id: str
    subject_revision: int


class CycleDraftRequest(BaseModel):
    goals: list[GoalOutline]
    unfinished_source_treatment: str | None = None


class PublicationRequest(BaseModel):
    gate_id: str
    subject_revision: int
    disposition: OutputDisposition
    output_reference: str | None = None


class MessageRequest(BaseModel):
    message: str


class MessageResponse(BaseModel):
    """One conversation turn: the assistant reply, whether the roadmap was
    committed, and the (possibly advanced) phase."""

    reply: str
    committed: bool
    phase: str


class ChatMessageResponse(BaseModel):
    role: str
    content: str
    created_at: str
    meta: dict[str, Any]


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
        "update_task",
        "update_goal",
        "remove_goal",
    ]
    goal_id: str
    task_id: str | None = None
    task: NewTaskBody | None = None
    ordered_task_ids: list[str] | None = None
    required_capabilities: list[str] | None = None
    agent_id: str | None = None
    name: str | None = None
    description: str | None = None
    depends_on: list[str] | None = None


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
    if body.type == "update_task":
        return UpdateTask(
            goal_id=body.goal_id,
            task_id=_require(body.task_id, "task_id", body.type),
            name=body.name,
            description=body.description,
        )
    if body.type == "update_goal":
        return UpdateGoal(
            goal_id=body.goal_id,
            name=body.name,
            description=body.description,
            depends_on=body.depends_on,
        )
    if body.type == "remove_goal":
        return RemoveGoal(body.goal_id)
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
    container.project_repo.get(body.project_id)
    plan_id = create_plan(body.brief, body.project_id, request_id, container.new_unit_of_work())
    return PlanCreatedResponse(plan_id=plan_id)


@router.get("")
def list_plans(container: AppContainer = Depends(get_container)) -> list[dict]:
    return container.new_unit_of_work().plans.list_summaries()


@router.get("/{plan_id}", response_model=PlanDetailResponse)
def get_plan(plan_id: str, container: AppContainer = Depends(get_container)) -> PlanDetailResponse:
    uow = container.new_unit_of_work()
    with uow:
        plan = uow.plans.get(plan_id)
        open_attempts = uow.executions.list_open_attempts(plan_id)
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
        pause_requested=plan.pause_requested,
        paused=plan.paused,
        paused_reason=plan.paused_reason,
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
        active_cycle=cycle.model_dump(mode="json") if cycle is not None else None,
        pending_gate=(
            plan.review_gate.model_dump(mode="json")
            if plan.review_gate is not None and plan.review_gate.unresolved
            else None
        ),
        block=(
            plan.block.model_dump(mode="json")
            if plan.block is not None and plan.block.active
            else None
        ),
        goals=[goal.model_dump(mode="json") for goal in goals],
        cycles=[item.model_dump(mode="json") for item in plan.cycles],
        intent_proposal=(
            plan.intent_proposal.model_dump(mode="json")
            if plan.intent_proposal is not None
            else None
        ),
        cycle_draft=(
            plan.cycle_draft.model_dump(mode="json") if plan.cycle_draft is not None else None
        ),
        legacy_phase=plan.legacy_phase,
        phase=plan.phase.value,
        iteration=plan.iteration,
    )


@router.post("/{plan_id}/intent", status_code=201)
def propose_intent_route(
    plan_id: str,
    body: IntentProposalRequest,
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    proposal = propose_intent(
        plan_id,
        objective=body.objective,
        scope=body.scope,
        constraints=body.constraints,
        exclusions=body.exclusions,
        kind=body.kind,
        planner_session_ref=body.planner_session_ref,
        uow=container.new_unit_of_work(),
        clock=container.clock,
    )
    return proposal.model_dump(mode="json")


@router.put("/{plan_id}/intent")
def revise_intent_route(
    plan_id: str,
    body: IntentProposalRequest,
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    proposal = revise_intent(
        plan_id,
        objective=body.objective,
        scope=body.scope,
        constraints=body.constraints,
        exclusions=body.exclusions,
        planner_session_ref=body.planner_session_ref,
        uow=container.new_unit_of_work(),
        clock=container.clock,
    )
    return proposal.model_dump(mode="json")


@router.delete("/{plan_id}/intent", status_code=204)
def cancel_intent_route(
    plan_id: str,
    container: AppContainer = Depends(get_container),
) -> None:
    cancel_intent(
        plan_id,
        uow=container.new_unit_of_work(),
        clock=container.clock,
    )


@router.post("/{plan_id}/intent/approve", status_code=204)
def approve_intent_route(
    plan_id: str,
    body: ReviewDecisionRequest,
    container: AppContainer = Depends(get_container),
) -> None:
    approve_intent(
        plan_id,
        body.gate_id,
        body.subject_revision,
        container.new_unit_of_work(),
        container.clock,
    )


@router.post("/{plan_id}/cycle-draft", status_code=201)
def submit_cycle_draft_route(
    plan_id: str,
    body: CycleDraftRequest,
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    draft = submit_cycle_draft_use_case(
        plan_id,
        goals=body.goals,
        unfinished_source_treatment=body.unfinished_source_treatment,
        uow=container.new_unit_of_work(),
    )
    return draft.model_dump(mode="json")


@router.put("/{plan_id}/cycle-draft")
def revise_cycle_draft_route(
    plan_id: str,
    body: CycleDraftRequest,
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    draft = revise_cycle_draft(
        plan_id,
        goals=body.goals,
        unfinished_source_treatment=body.unfinished_source_treatment,
        uow=container.new_unit_of_work(),
        clock=container.clock,
    )
    return draft.model_dump(mode="json")


@router.delete("/{plan_id}/cycle-draft", status_code=204)
def cancel_cycle_draft_route(
    plan_id: str,
    container: AppContainer = Depends(get_container),
) -> None:
    cancel_cycle_draft(
        plan_id,
        uow=container.new_unit_of_work(),
        clock=container.clock,
    )


@router.post("/{plan_id}/cycle-draft/approve", status_code=201)
def activate_cycle_route(
    plan_id: str,
    body: ReviewDecisionRequest,
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    cycle = activate_cycle(
        plan_id,
        body.gate_id,
        body.subject_revision,
        container.new_unit_of_work(),
        container.clock,
    )
    return cycle.model_dump(mode="json")


@router.post("/{plan_id}/publication", status_code=204)
def publish_cycle_route(
    plan_id: str,
    body: PublicationRequest,
    container: AppContainer = Depends(get_container),
) -> None:
    record_output_disposition(
        plan_id,
        body.gate_id,
        body.subject_revision,
        body.disposition,
        body.output_reference,
        container.new_unit_of_work(),
        container.clock,
    )


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


class PauseRequest(BaseModel):
    reason: str | None = None


@router.post("/{plan_id}/pause", status_code=204)
def pause(
    plan_id: str,
    body: PauseRequest | None = None,
    container: AppContainer = Depends(get_container),
) -> None:
    """Arm the pause gate: the worker stops claiming the plan at the next unit
    boundary and goals/tasks become editable. Idempotent."""
    reason = body.reason if body is not None else None
    pause_plan(plan_id, container.new_unit_of_work(), reason)


@router.post("/{plan_id}/resume", status_code=204)
def resume(plan_id: str, container: AppContainer = Depends(get_container)) -> None:
    """Clear the pause gate and requeue failed work (the manual retry): FAILED
    tasks return to PENDING with a fresh attempt budget. 422 when not paused."""
    resume_plan(plan_id, container.new_unit_of_work())


class RetryTaskRequest(BaseModel):
    goal_id: str
    task_id: str


@router.post("/{plan_id}/retry", status_code=204)
def retry_blocked_task(
    plan_id: str,
    body: RetryTaskRequest,
    container: AppContainer = Depends(get_container),
) -> None:
    """Retry only the selected failed task; resume remains a separate command."""
    retry_task(
        plan_id,
        body.goal_id,
        body.task_id,
        container.new_unit_of_work(),
        container.clock,
    )


@router.post("/{plan_id}/approve", status_code=204)
def approve(plan_id: str, container: AppContainer = Depends(get_container)) -> None:
    """Human approval at the pre-execution gate: AWAITING_REVIEW -> RUNNING."""
    control.resume_from_review(plan_id, container.new_unit_of_work())


@router.post("/{plan_id}/review/reopen", status_code=204)
def reopen(plan_id: str, container: AppContainer = Depends(get_container)) -> None:
    """Human "request changes" at the pre-execution gate: AWAITING_REVIEW ->
    DISCOVERY. Re-opens the planning chat; the next commit replaces the roadmap."""
    control.reopen_discovery(plan_id, container.new_unit_of_work())


@router.post("/{plan_id}/review/finish", status_code=204)
def finish(plan_id: str, container: AppContainer = Depends(get_container)) -> None:
    """Human "finish" at the post-execution gate: REVIEW -> DONE."""
    control.finish_review(plan_id, container.new_unit_of_work())


@router.post("/{plan_id}/review/replan", status_code=204)
def replan_from_review(plan_id: str, container: AppContainer = Depends(get_container)) -> None:
    """Human "replan next phase" at the post-execution gate: REVIEW -> REPLANNING."""
    control.review_replan(plan_id, container.new_unit_of_work())


@router.post("/{plan_id}/replan", status_code=204)
def replan_mid_running(plan_id: str, container: AppContainer = Depends(get_container)) -> None:
    """Chat-triggered mid-RUNNING replan: skip pending work -> REPLANNING."""
    request_replan(plan_id, container.new_unit_of_work())


@router.post("/{plan_id}/discovery/message", response_model=MessageResponse)
async def discovery(
    plan_id: str,
    body: MessageRequest,
    container: AppContainer = Depends(get_container),
) -> MessageResponse:
    """One DISCOVERY conversation turn. Multi-turn: committed=false keeps the
    conversation open; committed=true is the roadmap commit -> ARCHITECTURE."""
    result = await discovery_message(
        plan_id,
        body.message,
        container.new_unit_of_work(),
        container.reasoner,
        container.chat_store,
        container.clock,
    )
    return MessageResponse(reply=result.reply, committed=result.committed, phase=result.phase.value)


@router.post("/{plan_id}/replanning/message", response_model=MessageResponse)
async def replanning(
    plan_id: str,
    body: MessageRequest,
    container: AppContainer = Depends(get_container),
) -> MessageResponse:
    """One REPLANNING conversation turn. committed=true commits the new goal
    set -> ARCHITECTURE (the iteration increments here)."""
    result = await replanning_message(
        plan_id,
        body.message,
        container.new_unit_of_work(),
        container.reasoner,
        container.chat_store,
        container.clock,
    )
    return MessageResponse(reply=result.reply, committed=result.committed, phase=result.phase.value)


class AgentEventResponse(BaseModel):
    id: int
    event_id: str
    plan_id: str
    task_id: str | None
    attempt: int
    seq: int
    type: str
    payload: dict[str, Any]
    occurred_at: str


@router.get("/{plan_id}/agent-events", response_model=list[AgentEventResponse])
def agent_events(
    plan_id: str,
    task_id: str | None = None,
    limit: int = 200,
    before_id: int | None = None,
    container: AppContainer = Depends(get_container),
) -> list[AgentEventResponse]:
    """The plan's fine-grained agent/reasoner telemetry history (most-recent
    first), optionally filtered to one task. 404s for an unknown plan."""
    import json

    uow = container.new_unit_of_work()
    with uow:
        uow.plans.get(plan_id)  # existence check -> PLAN_NOT_FOUND -> 404
    rows = container.agent_event_reader.list(
        plan_id, task_id=task_id, limit=limit, before_id=before_id
    )
    return [
        AgentEventResponse(
            id=r["id"],
            event_id=r["event_id"],
            plan_id=r["plan_id"],
            task_id=r["task_id"],
            attempt=r["attempt"],
            seq=r["seq"],
            type=r["type"],
            payload=json.loads(r["payload"]) if r["payload"] else {},
            occurred_at=r["occurred_at"],
        )
        for r in rows
    ]


@router.get("/{plan_id}/chat", response_model=list[ChatMessageResponse])
def chat_history(
    plan_id: str, container: AppContainer = Depends(get_container)
) -> list[ChatMessageResponse]:
    """The plan's DISCOVERY/REPLANNING conversation, in order. 404s for an
    unknown plan (chat rows only exist for real plans)."""
    uow = container.new_unit_of_work()
    with uow:
        uow.plans.get(plan_id)  # existence check -> PLAN_NOT_FOUND -> 404
    return [
        ChatMessageResponse(
            role=m.role,
            content=m.content,
            created_at=m.created_at.isoformat(),
            meta=dict(m.meta),
        )
        for m in container.chat_store.list(plan_id)
    ]
