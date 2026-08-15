"""Operator commands against a live plan: edits, pause/resume, retry, block
resolution, agent rebinding, the retry policy, and the review/replan commands."""

from __future__ import annotations


from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from praxis_orchestrator.api.dependencies import get_container
from praxis_orchestrator.app.use_cases import control
from praxis_orchestrator.app.use_cases.apply_edit import (
    apply_edit,
)
from praxis_orchestrator.app.use_cases.operator_commands import (
    rebind_goal_agents,
    pause_plan,
    resume_plan,
    retry_planning_stage,
    retry_task,
)
from praxis_orchestrator.app.use_cases.request_replan import request_replan
from praxis_orchestrator.app.use_cases.update_retry_policy import update_retry_policy
from praxis_orchestrator.infra.container import AppContainer


from praxis_orchestrator.api.routers.plans.schemas import (
    EditRequest,
    _to_edit,
)

router = APIRouter(prefix="/plans", tags=["plans"])


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


@router.post("/{plan_id}/goals/{goal_id}/rebind-agents", status_code=204)
def rebind_goal_agents_endpoint(
    plan_id: str,
    goal_id: str,
    container: AppContainer = Depends(get_container),
) -> None:
    """Re-resolve this goal's UNFINISHED tasks against the live agent registry.

    The supported way to move in-flight work to a different runtime. Before it
    existed the only route was deleting the plan, which throws away the
    approved intent, the frozen contracts and every piece of accepted evidence
    for what is merely a change of agent.

    Requires the plan to be PAUSED (409 otherwise): rebinding a running plan
    would swap the agent out from under an executing attempt. Finished tasks
    are never rebound — their evidence belongs to the agent that produced it.
    """
    rebind_goal_agents(
        plan_id,
        goal_id,
        container.new_unit_of_work(),
        container.clock,
        container.agent_repo,
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


