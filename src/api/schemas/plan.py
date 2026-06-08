"""src/api/schemas/plan.py — Plan-related API DTOs."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class PlanBriefResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    vision: str
    constraints: list[str]
    phase_1_exit_criteria: list[str]
    open_questions: list[str]


class PlanPhaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    index: int
    name: str
    goal: str
    goal_names: list[str]
    status: str
    exit_criteria: list[str]
    lessons: list[str]


class PlanHistoryEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event: str
    timestamp: Optional[str] = None
    actor: Optional[str] = None
    detail: Optional[dict[str, Any]] = None


class PlanResponse(BaseModel):
    """Full plan read-model."""
    plan_id: Optional[str]
    status: str
    vision: str
    architecture_summary: Optional[str] = None
    current_phase_index: int
    state_version: int
    phases: list[PlanPhaseResponse]
    brief: Optional[PlanBriefResponse]
    history: list[PlanHistoryEntryResponse]


# ── Approve-Brief ─────────────────────────────────────────────────────────────

class ApproveBriefResponse(BaseModel):
    plan_status: str
    vision: str


# ── Approve-Architecture ──────────────────────────────────────────────────────

class ApproveArchitectureRequest(BaseModel):
    decision_ids: list[str]


class ApproveArchitectureResponse(BaseModel):
    decisions_applied: int
    goals_dispatched: list[str]
    plan_status: str


# ── Approve-Phase ─────────────────────────────────────────────────────────────

class ApprovePhaseRequest(BaseModel):
    approve_next: bool = True


class ApprovePhaseResponse(BaseModel):
    decisions_applied: int
    goals_dispatched: list[str]
    plan_status: str
