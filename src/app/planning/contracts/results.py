from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.domain.aggregates.project_plan import Phase, ProjectBrief
from src.domain.ports.project_state import DecisionEntry
from src.domain.value_objects.architecture_roadmap import ArchitectureRoadmap


@dataclass
class DiscoveryResult:
    session_id: str
    brief: Optional[ProjectBrief]
    needs_approval: bool
    failure_reason: Optional[str] = None


@dataclass
class ArchitectureResult:
    session_id: str
    roadmap: Optional[ArchitectureRoadmap]
    needs_approval: bool
    failure_reason: Optional[str] = None

    @property
    def pending_decisions(self) -> list[DecisionEntry]:
        """Decisions from the typed roadmap (empty when the run failed)."""
        return self.roadmap.decisions if self.roadmap else []

    @property
    def pending_phases(self) -> list[Phase]:
        """Phases from the typed roadmap (empty when the run failed)."""
        return self.roadmap.phases if self.roadmap else []


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
