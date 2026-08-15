"""Request/response shapes and the pure projections that build them.

Split out of the 1,626-line `plans.py` (P8.7 task 5). Every route module imports
from here, so a DTO has exactly one definition and the read model cannot drift
between the document that serves it and the document that describes it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from praxis_orchestrator.app.block_policy import requires_human
from praxis_orchestrator.app.use_cases.apply_edit import (
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
)
from praxis_orchestrator.domain.entities.execution_contracts import ContractCriterion, VerificationStrategy
from praxis_orchestrator.domain.entities.goal import Goal
from praxis_orchestrator.domain.aggregates.planner_orchestrator import Plan
from praxis_orchestrator.domain.entities.planning_artifacts import (
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
from praxis_orchestrator.domain.entities.task import Task
from praxis_orchestrator.domain.errors.planning_errors import InvalidEditError
from praxis_orchestrator.domain.factories.identity import new_id

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

    `requires_human` is a pure function of `kind` (`praxis_orchestrator/app/block_policy.py`),
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


