"""src/api/schemas/plan.py — Plan-related API DTOs.

Field types strictly mirror the ProjectPlan aggregate
(src/domain/aggregates/project_plan.py) — keep them in sync.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

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


# ── Approve-Architecture ──────────────────────────────────────────────────────

class ApproveArchitectureRequest(BaseModel):
    decision_ids: list[str]


class ApproveArchitectureResponse(BaseModel):
    decisions_applied: int
    goals_dispatched: list[str]
    plan_status: ProjectPlanStatus


# ── Approve-Phase ─────────────────────────────────────────────────────────────

class ApprovePhaseRequest(BaseModel):
    approve_next: bool = True


class ApprovePhaseResponse(BaseModel):
    decisions_applied: int
    goals_dispatched: list[str]
    plan_status: ProjectPlanStatus
