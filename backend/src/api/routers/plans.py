"""
/api/plans — the plan lifecycle: create, inspect, edit, and the human commands
that drive the two gates and the replan loop. Routes map 1:1 onto use cases;
errors bubble to the global mapping layer.
"""

from __future__ import annotations

from datetime import datetime
import uuid
import json
from typing import Any, AsyncIterator, Literal

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.api.dependencies import get_container
from src.app.block_policy import requires_human
from src.app.execution_records import (
    ExecutionAttemptStatus,
    PlanningOperation,
    PlanningOperationStatus,
)
from src.app.ports import ChatMessage
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
    UpdateTaskContract,
    apply_edit,
)
from src.app.use_cases.conversation import discovery_message, replanning_message
from src.app.use_cases.create_plan import open_project_plan
from src.app.use_cases.bind_project import bind_legacy_project
from src.app.use_cases.delete_plan import delete_plan
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
from src.app.use_cases.pause_resume import (
    pause_plan,
    resume_plan,
    retry_planning_stage,
    retry_task,
)
from src.app.use_cases.request_replan import request_replan
from src.app.use_cases.update_retry_policy import update_retry_policy
from src.domain.errors.base import DomainError
from src.domain.entities.execution_contracts import ContractCriterion, VerificationStrategy
from src.domain.entities.goal import Goal
from src.domain.aggregates.planner_orchestrator import Plan
from src.domain.entities.planning_artifacts import (
    Cycle,
    CycleDraft,
    GoalOutline,
    IntentProposal,
    OutputDisposition,
    PlanBlock,
    ProposalKind,
    ReviewGate,
    ReviewSubjectType,
)
from src.domain.entities.task import Task
from src.domain.errors.planning_errors import InvalidEditError
from src.domain.factories.identity import new_id
from src.infra.container import AppContainer
from src.infra.db.unit_of_work import SqliteUnitOfWork
from src.infra.errors import AttemptNotFoundError
from src.infra.runtime.process_supervisor import attempt_log_path, follow_attempt_log

router = APIRouter(prefix="/plans", tags=["plans"])


# ---- DTOs ----
class CreatePlanRequest(BaseModel):
    brief: str
    project_id: str


class ProjectBindingRequest(BaseModel):
    project_id: str


class PlanCreatedResponse(BaseModel):
    plan_id: str
    created: bool
    opened_existing: bool
    brief_preserved: bool
    discovery_operation_id: str | None
    discovery_status: str | None
    discovery_reply: str | None
    discovery_error: str | None


class ActiveRunResponse(BaseModel):
    run_id: str
    attempt_id: str
    attempt_number: int
    goal_id: str
    task_id: str
    started_at: str


class WorkerLeaseResponse(BaseModel):
    """Whether the claim behind `activity` is ALIVE, or a dead worker's orphan.

    Phase 2 exit criterion 3: an operator must be able to tell active work from
    every other state using persisted facts. Everything else on this document
    already distinguishes waiting, paused, blocked, and failed — but "running"
    covered two opposite realities. Measured on 2026-07-27: `kill -9` on a
    worker holding a RUNNING attempt left the plan reporting `status: running`
    with its task `running` and `retry_not_before: null` for the full 300s goal
    lease, indistinguishable from genuine work, because the only thing served
    was the active run's START time.

    `expires_at` is renewed by the heartbeat every third of the lease, so it is
    the liveness signal: comfortably in the future means a live worker is
    checking in; in the past means nobody is coming back and the work resumes
    when the lease expires. `expired` is served rather than left to the client
    to compute against its own clock, which may not be the server's.

    `scope` names WHICH lease, because they mean different things: a `goal`
    lease is held by the goal worker actually running attempts, a `plan` claim
    by the tick doing planning and gates.
    """

    scope: Literal["goal", "plan"]
    goal_id: str | None
    worker_id: str
    expires_at: str
    expired: bool
    seconds_remaining: int


class ProviderWaitingResponse(BaseModel):
    """An open provider capacity circuit gating this plan's work.

    A SIBLING field, not folded into `status_reason`: that is a pure property of
    the Plan aggregate, and the aggregate cannot see a RuntimeCircuit (it lives in
    the execution-record store). Mixing it in would mean either a domain change or
    a router overwriting a domain property. Same pattern `active_run` and
    `planning_progress` already use.

    The root stays RUNNING while this is set — it IS running, merely unclaimable
    until `retry_at`. Waiting on a provider is not a lifecycle state.
    """

    provider_id: str
    model_id: str | None  # None = a provider-wide (account-level) circuit
    runtime: str
    limit_scope: str | None
    retry_at: str
    since: str  # when the outage started, not when it last failed
    failure_count: int
    safe_message: str
    needs_attention: bool  # the outage outlived its ceiling and opened a block


class BlockResponse(BaseModel):
    """The domain `PlanBlock` plus the one fact only `block_policy` knows.

    `requires_human` is a pure function of `kind` (`src/app/block_policy.py`),
    so it is projected here rather than persisted: a policy change must not
    leave stale copies in old rows, and adding a field to the FROZEN
    `PlanBlock` would need a domain un-freeze for a value the block can
    already derive from a field it carries today.
    """

    id: str
    kind: str
    stage: str
    explanation: str
    goal_id: str | None
    task_id: str | None
    task_revision: int | None
    run_id: str | None
    evidence_refs: list[str]
    legal_resolutions: list[str]
    requires_human: bool
    created_at: datetime
    resolved_at: datetime | None
    resolution: str | None


def _block_response(block: PlanBlock) -> BlockResponse:
    """Project a domain block into its served shape.

    Fields are listed explicitly rather than via `**block.model_dump()` so
    that a future field added to `PlanBlock` fails loudly here (a KeyError on
    an unexpected/missing name) instead of silently passing through — see
    the design's "no catch-all" rule (P4.2 §3.4).
    """
    return BlockResponse(
        id=block.id,
        kind=block.kind,
        stage=block.stage,
        explanation=block.explanation,
        goal_id=block.goal_id,
        task_id=block.task_id,
        task_revision=block.task_revision,
        run_id=block.run_id,
        evidence_refs=block.evidence_refs,
        legal_resolutions=block.legal_resolutions,
        requires_human=requires_human(block.kind),
        created_at=block.created_at,
        resolved_at=block.resolved_at,
        resolution=block.resolution,
    )


# Where each advertised action is actually served. `Plan.legal_actions`
# publishes raw strings, and `block_policy.py` has mapped them to routes in a
# COMMENT — which no client can read and no test can execute. This is that
# comment, promoted to data.
#
# `{plan_id}` stays a template rather than being interpolated: the value is then
# directly comparable to `app.openapi()["paths"]`, which is what lets
# test_legal_actions_contract.py verify the map instead of trusting it.
_ACTION_ROUTES: dict[str, str] = {
    "pause": "POST /api/plans/{plan_id}/pause",
    "resume": "POST /api/plans/{plan_id}/resume",
    "start_replan": "POST /api/plans/{plan_id}/replan",
    "start_intent": "POST /api/plans/{plan_id}/intent",
    "edit_pending_work": "POST /api/plans/{plan_id}/edits",
    "edit_task": "POST /api/plans/{plan_id}/edits",
    "retry_stage": "POST /api/plans/{plan_id}/retry-stage",
    "wait_and_retry": "POST /api/plans/{plan_id}/retry",
    "bind_project": "POST /api/plans/{plan_id}/project-binding",
}

# A `review:<decision>` is served by a different route depending on WHICH gate
# is open — the one part of the vocabulary a client genuinely cannot derive,
# because only the server knows the open gate's subject.
_GATE_ROUTES: dict[ReviewSubjectType, str] = {
    ReviewSubjectType.INTENT: "POST /api/plans/{plan_id}/intent/approve",
    ReviewSubjectType.CYCLE_DRAFT: "POST /api/plans/{plan_id}/cycle-draft/approve",
    ReviewSubjectType.CYCLE_COMPLETION: "POST /api/plans/{plan_id}/publication",
}


def action_endpoints_for(plan: Plan) -> dict[str, str]:
    """The route serving each currently-advertised action.

    An action with no known route is OMITTED rather than guessed: a wrong
    endpoint is worse than a missing one, because the client would call it and
    get a 404 it cannot interpret. `test_legal_actions_contract.py` asserts the
    omission never happens, so a new action cannot ship unmapped.
    """
    endpoints: dict[str, str] = {}
    for action in plan.legal_actions:
        if action.startswith("review:"):
            gate = plan.review_gate
            if gate is not None and gate.subject_type in _GATE_ROUTES:
                endpoints[action] = _GATE_ROUTES[gate.subject_type]
            continue
        route = _ACTION_ROUTES.get(action)
        if route is not None:
            endpoints[action] = route
    return endpoints


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
    # Parallel to `legal_actions`: the route serving each one. The strings stay
    # exactly as they were for existing clients; this is purely additive.
    action_endpoints: dict[str, str]
    pause_requested: bool
    paused: bool
    paused_reason: str | None
    active_run: ActiveRunResponse | None
    worker_lease: WorkerLeaseResponse | None
    provider_waiting: ProviderWaitingResponse | None
    planning_operation: dict[str, Any] | None
    planning_progress: str | None
    active_cycle: Cycle | None
    pending_gate: ReviewGate | None
    block: BlockResponse | None
    # Domain unfreeze #14 — per-goal blocks: goal_id -> that goal's own active
    # (or resolved-but-recent) PlanBlock, independent of the plan-wide `block`
    # scalar above. Only entries with `.active` True are currently unresolved;
    # callers resolving one pass its goal_id to POST /retry (or the relevant
    # resolution endpoint) exactly as they already do for `block.goal_id`.
    goal_blocks: dict[str, BlockResponse]
    goals: list[Goal]
    cycles: list[Cycle]
    intent_proposal: IntentProposal | None
    cycle_draft: CycleDraft | None
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
    operation_id: str
    operation_status: str
    error: str | None = None


class ChatMessageResponse(BaseModel):
    role: str
    content: str
    created_at: str
    meta: dict[str, Any]


class PlanningOperationResponse(BaseModel):
    id: str
    purpose: str
    target_goal_id: str | None
    status: str
    created_at: str
    updated_at: str
    started_at: str | None
    completed_at: str | None
    last_liveness_at: str | None
    model_request_count: int
    tool_turn_count: int
    runtime: str | None
    provider_id: str | None
    model_id: str | None
    failure_kind: str | None
    retry_at: str | None
    safe_message: str | None


class ExecutionAttemptResponse(BaseModel):
    id: str
    number: int
    task_attempt: int
    status: str
    started_at: str
    completed_at: str | None
    last_liveness_at: str | None
    timeout_seconds: int | None
    runtime: str | None
    provider_id: str | None
    model_id: str | None
    failure_kind: str | None
    provider_code: str | None
    retryable: bool | None
    retry_at: str | None
    limit_scope: str | None
    exit_code: int | None
    safe_message: str | None
    stdout_tail: str
    stderr_tail: str


class ExecutionRunTimelineResponse(BaseModel):
    id: str
    goal_id: str
    task_id: str
    status: str
    started_at: str
    completed_at: str | None
    attempts: list[ExecutionAttemptResponse]


class TaskExecutionTimelineResponse(BaseModel):
    goal_id: str
    task_id: str
    runs: list[ExecutionRunTimelineResponse]


class AttemptLogEntryResponse(BaseModel):
    monotonic_seconds: float
    stream: Literal["stdout", "stderr"]
    text: str


class AttemptLogResponse(BaseModel):
    entries: list[AttemptLogEntryResponse]
    truncated: bool


class AttemptTimelineResponse(BaseModel):
    planning_operations: list[PlanningOperationResponse]
    tasks: list[TaskExecutionTimelineResponse]


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
        "update_task_contract",
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
    # update_task_contract (un-freeze #17): every field the reasoner authored.
    # Execution identity and observed evidence stay read-only by omission.
    objective: str | None = None
    acceptance_criteria: list[ContractCriterion] | None = None
    verification_strategy: VerificationStrategy | None = None
    allowed_scope: list[str] | None = None
    forbidden_scope: list[str] | None = None
    verification_commands: list[str] | None = None
    goal_criterion_ids: list[str] | None = None


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
    if body.type == "update_task_contract":
        return UpdateTaskContract(
            goal_id=body.goal_id,
            task_id=_require(body.task_id, "task_id", body.type),
            objective=body.objective,
            acceptance_criteria=body.acceptance_criteria,
            verification_strategy=body.verification_strategy,
            allowed_scope=body.allowed_scope,
            forbidden_scope=body.forbidden_scope,
            verification_commands=body.verification_commands,
            goal_criterion_ids=body.goal_criterion_ids,
            required_capabilities=body.required_capabilities,
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


@router.delete("/{plan_id}", status_code=204)
def delete_plan_route(plan_id: str, container: AppContainer = Depends(get_container)) -> None:
    """Dispose of a plan and everything produced under it.

    404 when it does not exist, 409 (`PLAN_BUSY`) while a worker holds a live
    lease. Irreversible: cycles, attempts, evidence, chat and telemetry go with
    it, so export the run evidence first if it is worth keeping.
    """
    delete_plan(plan_id, container.new_unit_of_work())


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


class PlanningArtifactResponse(BaseModel):
    """One recorded planning attempt.

    Without this the feature is invisible: a retry that starts better informed
    looks identical from outside to one that does not, so nothing an operator
    (or a fixture) can read tells them whether the memory is working.
    """

    goal_id: str | None
    purpose: str
    sequence: int
    outcome: str
    input_fingerprint: str
    rejection_reasons: list[str]
    turns_used: int | None
    has_payload: bool
    created_at: str


@router.get("/{plan_id}/planning-artifacts", response_model=list[PlanningArtifactResponse])
def list_planning_artifacts(
    plan_id: str,
    purpose: str = "goal_contract",
    goal_id: str | None = None,
    limit: int = 20,
    container: AppContainer = Depends(get_container),
) -> list[PlanningArtifactResponse]:
    """What earlier attempts at this artifact established, newest first.

    The payload itself is deliberately NOT served — it is model working state,
    can be large, and an operator needs to know an attempt happened and why it
    was refused, not to re-read the draft.
    """
    artifacts = container.planning_artifacts.latest(
        plan_id, purpose, goal_id=goal_id, limit=limit
    )
    return [
        PlanningArtifactResponse(
            goal_id=item.goal_id,
            purpose=item.purpose,
            sequence=item.sequence,
            outcome=item.outcome,
            input_fingerprint=item.input_fingerprint,
            rejection_reasons=list(item.rejection_reasons),
            turns_used=item.turns_used,
            has_payload=item.payload is not None,
            created_at=item.created_at.isoformat(),
        )
        for item in artifacts
    ]


@router.delete("/{plan_id}/planning-artifacts", status_code=204)
def clear_planning_artifacts(
    plan_id: str,
    purpose: str = "goal_contract",
    goal_id: str | None = None,
    container: AppContainer = Depends(get_container),
) -> None:
    """Drop a goal's planning memory — the escape hatch for when the replay
    heuristics are wrong and a retry keeps being steered by a bad draft."""
    container.planning_artifacts.clear(plan_id, purpose, goal_id=goal_id)


@router.get("/{plan_id}/attempts", response_model=AttemptTimelineResponse)
def attempt_timeline(
    plan_id: str,
    container: AppContainer = Depends(get_container),
) -> AttemptTimelineResponse:
    """Durable task -> run -> attempt history, hydrated before live SSE."""
    with container.new_unit_of_work() as uow:
        uow.plans.get(plan_id)
        runs = uow.executions.list_runs(plan_id)
        attempts = uow.executions.list_attempts(plan_id)
        operations = uow.executions.list_planning_operations(plan_id)

    attempts_by_run: dict[str, list] = {}
    for attempt in attempts:
        attempts_by_run.setdefault(attempt.run_id, []).append(attempt)
    runs_by_task: dict[tuple[str, str], list] = {}
    for run in runs:
        runs_by_task.setdefault((run.goal_id, run.task_id), []).append(run)

    return AttemptTimelineResponse(
        planning_operations=[
            PlanningOperationResponse(
                id=item.id,
                purpose=item.purpose,
                target_goal_id=item.target_goal_id,
                status=item.status.value,
                created_at=item.created_at.isoformat(),
                updated_at=item.updated_at.isoformat(),
                started_at=item.started_at.isoformat() if item.started_at else None,
                completed_at=item.completed_at.isoformat() if item.completed_at else None,
                last_liveness_at=(
                    item.last_liveness_at.isoformat() if item.last_liveness_at else None
                ),
                model_request_count=item.model_request_count,
                tool_turn_count=item.tool_turn_count,
                runtime=item.runtime,
                provider_id=item.provider_id,
                model_id=item.model_id,
                failure_kind=item.failure_kind,
                retry_at=item.retry_at.isoformat() if item.retry_at else None,
                safe_message=item.safe_message,
            )
            for item in operations
        ],
        tasks=[
            TaskExecutionTimelineResponse(
                goal_id=goal_id,
                task_id=task_id,
                runs=[
                    ExecutionRunTimelineResponse(
                        id=run.id,
                        goal_id=run.goal_id,
                        task_id=run.task_id,
                        status=run.status.value,
                        started_at=run.started_at.isoformat(),
                        completed_at=(run.completed_at.isoformat() if run.completed_at else None),
                        attempts=[
                            ExecutionAttemptResponse(
                                id=attempt.id,
                                number=attempt.number,
                                task_attempt=attempt.task_attempt,
                                status=attempt.status.value,
                                started_at=attempt.started_at.isoformat(),
                                completed_at=(
                                    attempt.completed_at.isoformat()
                                    if attempt.completed_at
                                    else None
                                ),
                                last_liveness_at=(
                                    attempt.last_liveness_at.isoformat()
                                    if attempt.last_liveness_at
                                    else None
                                ),
                                timeout_seconds=attempt.timeout_seconds,
                                runtime=attempt.runtime,
                                provider_id=attempt.provider_id,
                                model_id=attempt.model_id,
                                failure_kind=attempt.failure_kind,
                                provider_code=attempt.provider_code,
                                retryable=attempt.retryable,
                                retry_at=(
                                    attempt.retry_at.isoformat() if attempt.retry_at else None
                                ),
                                limit_scope=(
                                    attempt.limit_scope.value if attempt.limit_scope else None
                                ),
                                exit_code=attempt.exit_code,
                                safe_message=attempt.safe_message,
                                stdout_tail=attempt.stdout_tail,
                                stderr_tail=attempt.stderr_tail,
                            )
                            for attempt in attempts_by_run.get(run.id, [])
                        ],
                    )
                    for run in task_runs
                ],
            )
            for (goal_id, task_id), task_runs in runs_by_task.items()
        ],
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
    """Remove only the manual pause. Retry/backoff state is untouched.

    A failed task must be retried with the targeted retry command. 422 when the
    plan is not manually paused.
    """
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


class RetryStageRequest(BaseModel):
    # Domain unfreeze #14: disambiguates which goal's agent_capability block
    # to retry when more than one goal is independently blocked at once.
    # Omit it when unambiguous (a plan-wide reasoner_failure block, or
    # exactly one active per-goal block) -- unset body stays backward
    # compatible with callers that send none at all.
    goal_id: str | None = None


@router.post("/{plan_id}/retry-stage", status_code=204)
def retry_blocked_planning_stage(
    plan_id: str,
    body: RetryStageRequest | None = None,
    container: AppContainer = Depends(get_container),
) -> None:
    """Retry a blocked reasoner stage or agent binding after registry repair."""
    retry_planning_stage(
        plan_id,
        container.new_unit_of_work(),
        container.clock,
        container.agent_repo,
        goal_id=body.goal_id if body is not None else None,
    )


class RetryPolicyUpdateRequest(BaseModel):
    """All fields optional: only the ones an operator sets are changed (partial
    merge over the plan's current retry policy); the rest keep their current
    value. Mirrors execution.retry_* config field-for-field.

    The bounds reject a policy that is not one — a budget of zero attempts, a
    negative wait, a multiplier that shrinks the backoff — rather than storing
    it and discovering it during an outage. They live on the DTO because the
    domain `RetryPolicy` is frozen.
    """

    max_attempts: int | None = Field(default=None, ge=1)
    initial_backoff_seconds: float | None = Field(default=None, ge=0)
    backoff_multiplier: float | None = Field(default=None, ge=1)
    max_backoff_seconds: float | None = Field(default=None, ge=0)
    jitter_ratio: float | None = Field(default=None, ge=0, le=1)


@router.post("/{plan_id}/retry-policy", status_code=204)
def update_retry_policy_route(
    plan_id: str,
    body: RetryPolicyUpdateRequest,
    container: AppContainer = Depends(get_container),
) -> None:
    """Retune an EXISTING plan's retry/backoff budget (un-freeze #12) — e.g.
    raise max_attempts/max_backoff_seconds so a plan stuck on a rate-limited
    provider keeps retrying automatically for longer before opening a block.
    Distinct from the execution.retry_* config keys, which only seed a NEW
    plan's policy at creation and never touch one already persisted."""
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    update_retry_policy(plan_id, updates, container.new_unit_of_work())


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
    return MessageResponse(
        reply=result.reply,
        committed=result.committed,
        phase=result.phase.value,
        operation_id=result.operation_id,
        operation_status=result.operation_status.value,
        error=result.error,
    )


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
    return MessageResponse(
        reply=result.reply,
        committed=result.committed,
        phase=result.phase.value,
        operation_id=result.operation_id,
        operation_status=result.operation_status.value,
        error=result.error,
    )


@router.get("/{plan_id}/attempts/{attempt_id}/log", response_model=AttemptLogResponse)
def attempt_log(
    plan_id: str,
    attempt_id: str,
    tail_lines: int = Query(default=200, ge=0, le=2000),
    container: AppContainer = Depends(get_container),
) -> AttemptLogResponse:
    with container.new_unit_of_work() as uow:
        try:
            attempt = uow.executions.get_attempt(attempt_id)
        except KeyError as exc:
            raise AttemptNotFoundError(attempt_id) from exc
        if attempt.plan_id != plan_id:
            raise AttemptNotFoundError(attempt_id)

    path = attempt_log_path(container.orchestrator_home, attempt_id)
    if not path.exists():
        return AttemptLogResponse(entries=[], truncated=False)

    entries: list[AttemptLogEntryResponse] = []
    truncated = False
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return AttemptLogResponse(entries=[], truncated=False)
    for line in lines:
        try:
            record = json.loads(line)
            if record.get("truncated") is True:
                truncated = True
                continue
            entries.append(AttemptLogEntryResponse.model_validate(record))
        except (ValueError, TypeError, AttributeError):
            continue
    return AttemptLogResponse(entries=entries[-tail_lines:] if tail_lines else [], truncated=truncated)


_TERMINAL_ATTEMPT_STATUSES = frozenset(
    {
        ExecutionAttemptStatus.SUCCEEDED,
        ExecutionAttemptStatus.FAILED,
        ExecutionAttemptStatus.ABANDONED,
    }
)


@router.get("/{plan_id}/attempts/{attempt_id}/log/stream")
async def attempt_log_stream(
    plan_id: str,
    attempt_id: str,
    request: Request,
    offset: int = Query(default=0, ge=0),
    container: AppContainer = Depends(get_container),
) -> StreamingResponse:
    """Live SSE tail of one attempt's RAW runtime stdout/stderr.

    Distinct from `/api/events` (telemetry): this streams the exact bytes the
    agent CLI wrote, straight from the bounded per-attempt runtime log, as they
    land. Each line is an SSE frame
    `id: <offset>` + `data: {monotonic_seconds,stream,text}`; an `event:
    truncated` frame means the bounded log rotated (reset your view); `event:
    end` closes the stream once the attempt reaches a terminal state. Resume
    without replay via the standard `Last-Event-ID` header (or `?offset=`).
    """
    with container.new_unit_of_work() as uow:
        try:
            attempt = uow.executions.get_attempt(attempt_id)
        except KeyError as exc:
            raise AttemptNotFoundError(attempt_id) from exc
        if attempt.plan_id != plan_id:
            raise AttemptNotFoundError(attempt_id)

    start = offset
    resume_id = request.headers.get("last-event-id")
    if resume_id and resume_id.isdigit():
        start = int(resume_id)

    path = attempt_log_path(container.orchestrator_home, attempt_id)

    def _is_terminal() -> bool:
        with container.new_unit_of_work() as uow:
            try:
                current = uow.executions.get_attempt(attempt_id)
            except KeyError:
                return True  # attempt vanished — nothing more will be written
            return current.status in _TERMINAL_ATTEMPT_STATUSES

    async def gen() -> AsyncIterator[str]:
        async for event in follow_attempt_log(
            path,
            is_terminal=_is_terminal,
            should_stop=request.is_disconnected,
            start_offset=start,
        ):
            if event.kind == "keepalive":
                yield ": keepalive\n\n"
            elif event.kind == "truncated":
                yield "event: truncated\ndata: {}\n\n"
            else:
                yield f"id: {event.offset}\ndata: {json.dumps(event.record)}\n\n"
        yield "event: end\ndata: {}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
