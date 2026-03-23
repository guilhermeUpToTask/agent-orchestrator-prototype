"""
src/domain/aggregates/project_plan.py — ProjectPlan aggregate.

Lifecycle:
  DISCOVERY → ARCHITECTURE → PHASE_ACTIVE → PHASE_REVIEW → PHASE_ACTIVE ...
                                                        → DONE

The ProjectPlan governs the overall project lifecycle and tracks phases,
brief, and architecture state.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class PhaseStatus(str, Enum):
    PLANNED  = "planned"
    ACTIVE   = "active"
    COMPLETED = "completed"


class ProjectPlanStatus(str, Enum):
    DISCOVERY     = "discovery"
    ARCHITECTURE  = "architecture"
    PHASE_ACTIVE  = "phase_active"
    PHASE_REVIEW  = "phase_review"
    DONE          = "done"


@dataclass(frozen=True)
class Phase:
    """A single phase in the project lifecycle."""
    index: int
    name: str               # "Foundation", "Core Domain", etc.
    goal: str               # one sentence — what does "done" mean for this phase
    goal_names: list[str]   # names of GoalAggregates dispatched in this phase
    status: PhaseStatus     # PLANNED | ACTIVE | COMPLETED
    lessons: str            # populated during PHASE_REVIEW, "" until then
    exit_criteria: str      # verifiable condition — populated during ARCHITECTURE

    def with_status(self, status: PhaseStatus) -> "Phase":
        """Return a new Phase with the given status."""
        return Phase(
            index=self.index,
            name=self.name,
            goal=self.goal,
            goal_names=list(self.goal_names),
            status=status,
            lessons=self.lessons,
            exit_criteria=self.exit_criteria,
        )

    def with_lessons(self, lessons: str) -> "Phase":
        """Return a new Phase with lessons recorded."""
        return Phase(
            index=self.index,
            name=self.name,
            goal=self.goal,
            goal_names=list(self.goal_names),
            status=self.status,
            lessons=lessons,
            exit_criteria=self.exit_criteria,
        )

    def register_goal(self, goal_name: str) -> "Phase":
        """Return a new Phase with the goal name appended."""
        new_names = list(self.goal_names)
        if goal_name not in new_names:
            new_names.append(goal_name)
        return Phase(
            index=self.index,
            name=self.name,
            goal=self.goal,
            goal_names=new_names,
            status=self.status,
            lessons=self.lessons,
            exit_criteria=self.exit_criteria,
        )


@dataclass(frozen=True)
class ProjectBrief:
    """Brief created at the end of DISCOVERY phase."""
    vision: str
    constraints: list[str]           # hard limits from discovery
    phase_1_exit_criteria: str       # what Phase 1 done means
    open_questions: list[str]        # unresolved at brief time


class HistoryEntry(BaseModel):
    """Record of a state change in the project plan."""
    event: str
    actor: str
    detail: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {"frozen": True}


class ProjectPlan(BaseModel):
    """
    Authoritative aggregate for project-wide planning state.

    All mutations go through named methods that call _bump() — never assign
    to fields directly from outside the aggregate.
    """

    plan_id: str
    status: ProjectPlanStatus = ProjectPlanStatus.DISCOVERY
    vision: str = ""                         # set during DISCOVERY, never changes except on pivot
    brief: Optional[ProjectBrief] = None     # set at end of DISCOVERY
    phases: list[Phase] = Field(default_factory=list)  # grows as project progresses
    current_phase_index: int = -1            # 0-based, -1 until first phase is planned
    architecture_summary: str = ""           # planner updates after each PHASE_REVIEW
    state_version: int = 1
    history: list[HistoryEntry] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {"frozen": True}

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create(cls, vision: str) -> "ProjectPlan":
        """Create a new project plan in DISCOVERY status."""
        return cls(
            plan_id=f"plan-{uuid4().hex[:12]}",
            vision=vision,
            status=ProjectPlanStatus.DISCOVERY,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _bump(self, event: str, actor: str, detail: dict[str, Any] | None = None) -> None:
        """Record a history entry and update version/timestamp."""
        # Note: This is called from methods that return new instances
        # since Pydantic models are frozen. We don't actually mutate self.

    def _assert_status(self, *expected: ProjectPlanStatus) -> None:
        if self.status not in expected:
            raise ValueError(
                f"ProjectPlan '{self.plan_id}' is '{self.status.value}'; "
                f"expected one of {[s.value for s in expected]}."
            )

    def _with_history(self, event: str, actor: str, detail: dict[str, Any] | None = None) -> "ProjectPlan":
        """Return a new instance with history entry added."""
        new_history = list(self.history)
        new_history.append(
            HistoryEntry(event=event, actor=actor, detail=detail or {})
        )
        return self.model_copy(
            update={
                "state_version": self.state_version + 1,
                "updated_at": datetime.now(timezone.utc),
                "history": new_history,
            }
        )

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def approve_brief(self, brief: ProjectBrief) -> "ProjectPlan":
        """
        DISCOVERY → ARCHITECTURE.

        Approve the project brief created during discovery.
        """
        self._assert_status(ProjectPlanStatus.DISCOVERY)

        return self._with_history(
            "project_plan.brief_approved",
            "operator",
            {"brief_vision": brief.vision},
        ).model_copy(
            update={
                "status": ProjectPlanStatus.ARCHITECTURE,
                "brief": brief,
                "vision": brief.vision,  # Update vision from brief
            }
        )

    def approve_phase(self, phases: list[Phase]) -> "ProjectPlan":
        """
        ARCHITECTURE → PHASE_ACTIVE (first phase)
        PHASE_REVIEW → PHASE_ACTIVE (subsequent phases)

        Approves the proposed phases and makes the first one ACTIVE.
        Appends new phases if not already present.
        """
        self._assert_status(ProjectPlanStatus.ARCHITECTURE, ProjectPlanStatus.PHASE_REVIEW)

        # Build new phases list - append if not already present
        new_phases = list(self.phases)
        phase_indices = {p.index for p in new_phases}

        for phase in phases:
            if phase.index not in phase_indices:
                new_phases.append(phase)
                phase_indices.add(phase.index)

        # Sort phases by index
        new_phases = sorted(new_phases, key=lambda p: p.index)

        # Set current phase index and mark as ACTIVE
        if phases:
            current_idx = phases[0].index
        else:
            current_idx = 0

        # Update phase statuses
        updated_phases = []
        for p in new_phases:
            if p.index == current_idx:
                updated_phases.append(p.with_status(PhaseStatus.ACTIVE))
            elif p.status == PhaseStatus.ACTIVE:
                updated_phases.append(p.with_status(PhaseStatus.COMPLETED))
            else:
                updated_phases.append(p)

        return self._with_history(
            "project_plan.phase_approved",
            "operator",
            {"phase_index": current_idx, "phase_count": len(phases)},
        ).model_copy(
            update={
                "status": ProjectPlanStatus.PHASE_ACTIVE,
                "phases": updated_phases,
                "current_phase_index": current_idx,
            }
        )

    def record_goal_registered(self, goal_name: str) -> "ProjectPlan":
        """
        Record that a goal was dispatched and registered to the current phase.

        Called when a goal is dispatched via GoalInitUseCase.
        """
        self._assert_status(ProjectPlanStatus.PHASE_ACTIVE)

        if self.current_phase_index < 0:
            raise ValueError("No active phase to register goal to")

        # Find and update the current phase
        updated_phases = []
        for p in self.phases:
            if p.index == self.current_phase_index:
                updated_phases.append(p.register_goal(goal_name))
            else:
                updated_phases.append(p)

        return self._with_history(
            "project_plan.goal_registered",
            "orchestrator",
            {"goal_name": goal_name, "phase_index": self.current_phase_index},
        ).model_copy(
            update={
                "phases": updated_phases,
            }
        )

    def trigger_review(self) -> "ProjectPlan":
        """
        PHASE_ACTIVE → PHASE_REVIEW.

        Called automatically when all goals in the active phase reach MERGED.
        """
        self._assert_status(ProjectPlanStatus.PHASE_ACTIVE)

        # Update current phase status to COMPLETED
        updated_phases = []
        for p in self.phases:
            if p.index == self.current_phase_index:
                updated_phases.append(p.with_status(PhaseStatus.COMPLETED))
            else:
                updated_phases.append(p)

        return self._with_history(
            "project_plan.phase_review_triggered",
            "orchestrator",
            {"phase_index": self.current_phase_index},
        ).model_copy(
            update={
                "status": ProjectPlanStatus.PHASE_REVIEW,
                "phases": updated_phases,
            }
        )

    def complete_review(self, lessons: str, architecture_summary: str) -> "ProjectPlan":
        """
        Record lessons learned and update architecture summary.

        Status stays PHASE_REVIEW until approve_phase() advances it.
        """
        self._assert_status(ProjectPlanStatus.PHASE_REVIEW)

        # Update current phase with lessons
        updated_phases = []
        for p in self.phases:
            if p.index == self.current_phase_index:
                updated_phases.append(p.with_lessons(lessons))
            else:
                updated_phases.append(p)

        return self._with_history(
            "project_plan.review_completed",
            "operator",
            {"phase_index": self.current_phase_index},
        ).model_copy(
            update={
                "phases": updated_phases,
                "architecture_summary": architecture_summary,
            }
        )

    def mark_done(self) -> "ProjectPlan":
        """
        PHASE_REVIEW → DONE.

        Called when no more phases are planned.
        """
        self._assert_status(ProjectPlanStatus.PHASE_REVIEW)

        return self._with_history(
            "project_plan.marked_done",
            "operator",
            {},
        ).model_copy(
            update={
                "status": ProjectPlanStatus.DONE,
            }
        )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def is_terminal(self) -> bool:
        return self.status == ProjectPlanStatus.DONE

    def current_phase(self) -> Optional[Phase]:
        """Get the currently active phase, if any."""
        if self.current_phase_index < 0:
            return None
        for p in self.phases:
            if p.index == self.current_phase_index:
                return p
        return None

    def planned_phases(self) -> list[Phase]:
        """Get all phases with PLANNED status."""
        return [p for p in self.phases if p.status == PhaseStatus.PLANNED]
