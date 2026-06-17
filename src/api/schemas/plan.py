"""src/api/schemas/plan.py — Plan-related API DTOs.

Field types strictly mirror the ProjectPlan aggregate
(src/domain/aggregates/project_plan.py) — keep them in sync.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict

from src.domain.aggregates.project_plan import PhaseStatus, ProjectPlanStatus


class PlanBriefResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    vision: str
    constraints: list[str]
    phase_1_exit_criteria: str
    open_questions: list[str]


class PlanPhaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    index: int
    name: str
    goal: str
    goal_names: list[str]
    status: PhaseStatus
    exit_criteria: str
    lessons: str


class PlanHistoryEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event: str
    timestamp: Optional[datetime] = None
    actor: Optional[str] = None
    detail: Optional[dict[str, Any]] = None


class PlanResponse(BaseModel):
    """Full plan read-model."""
    plan_id: Optional[str]
    status: ProjectPlanStatus
    vision: str
    architecture_summary: Optional[str] = None
    current_phase_index: int
    state_version: int
    phases: list[PlanPhaseResponse]
    brief: Optional[PlanBriefResponse]
    history: list[PlanHistoryEntryResponse]


# ── Approve-Brief ─────────────────────────────────────────────────────────────

class ApproveBriefResponse(BaseModel):
    plan_status: ProjectPlanStatus
    vision: str


# ── Architecture session status ───────────────────────────────────────────────

class ArchitectureStatusResponse(BaseModel):
    """Reload-resilient readiness of the autonomous architecture session.

    The approval gate is offered only when ``state == "completed"``; clients
    hydrate proposed decisions/phases from here so a page refresh mid/after the
    run keeps the correct state instead of relying on ephemeral SSE buffers.
    """
    state: Literal["none", "running", "completed", "failed"]
    session_id: Optional[str] = None
    decisions: list[dict[str, Any]] = []
    phases: list[dict[str, Any]] = []
    error: Optional[str] = None


# ── Approve-Architecture ──────────────────────────────────────────────────────

class ApproveArchitectureRequest(BaseModel):
    decision_ids: list[str]


class GoalDispatchFailureResponse(BaseModel):
    goal_name: str
    error: str


class ApproveArchitectureResponse(BaseModel):
    decisions_applied: int
    goals_dispatched: list[str]
    plan_status: ProjectPlanStatus
    goals_failed: list[GoalDispatchFailureResponse] = []


# ── Approve-Phase ─────────────────────────────────────────────────────────────

class ApprovePhaseRequest(BaseModel):
    approve_next: bool = True


class ApprovePhaseResponse(BaseModel):
    decisions_applied: int
    goals_dispatched: list[str]
    plan_status: ProjectPlanStatus
    goals_failed: list[GoalDispatchFailureResponse] = []


# ── Resume-Dispatch ───────────────────────────────────────────────────────────

class ResumeDispatchResponse(BaseModel):
    goals_dispatched: list[str]
    plan_status: ProjectPlanStatus
    goals_failed: list[GoalDispatchFailureResponse] = []
