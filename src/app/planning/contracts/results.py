from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.domain.aggregates.project_plan import Phase, ProjectBrief
from src.domain.ports.project_state import DecisionEntry


@dataclass
class DiscoveryResult:
    session_id: str
    brief: Optional[ProjectBrief]
    needs_approval: bool
    failure_reason: Optional[str] = None


@dataclass
class ArchitectureResult:
    session_id: str
    pending_decisions: list[DecisionEntry]
    pending_phases: list[Phase]
    needs_approval: bool
    failure_reason: Optional[str] = None


@dataclass
class PhaseReviewResult:
    session_id: str
    lessons: str
    next_phase_proposal: Optional[Phase]
    pending_decisions: list[DecisionEntry]
    needs_approval: bool
    failure_reason: Optional[str] = None


@dataclass
class ApprovalResult:
    decisions_applied: int
    goals_dispatched: list[str]
    plan_status: str
    spec_changes_applied: int = 0
