"""Versioned planning, review, block, and cycle domain objects."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from src.domain.entities.goal import Goal


class PlanStatus(str, Enum):
    RUNNING = "running"
    PAUSED = "paused"
    WAITING = "waiting"
    BLOCKED = "blocked"
    IDLE = "idle"


class CycleStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    SUPERSEDED = "superseded"
    CANCELLED = "cancelled"


class ProposalKind(str, Enum):
    INITIAL = "initial"
    REPLAN = "replan"


class OutputDisposition(str, Enum):
    OPEN_PR = "open_pr"
    MERGE = "merge"
    RETAIN_BRANCH = "retain_branch"
    DISCARD = "discard"


class ReviewSubjectType(str, Enum):
    INTENT = "intent"
    CYCLE_DRAFT = "cycle_draft"
    CYCLE_COMPLETION = "cycle_completion"


class ReviewResolution(BaseModel):
    decision: str
    resolved_at: datetime
    resolved_by: str | None = None
    note: str | None = None


class ReviewGate(BaseModel):
    id: str
    subject_type: ReviewSubjectType
    subject_id: str
    subject_revision: int
    allowed_decisions: list[str]
    continuation: str
    resolution: ReviewResolution | None = None
    invalidated_at: datetime | None = None

    @property
    def unresolved(self) -> bool:
        return self.resolution is None and self.invalidated_at is None


class PlanBlock(BaseModel):
    id: str
    kind: str
    explanation: str
    stage: str
    goal_id: str | None = None
    task_id: str | None = None
    task_revision: int | None = None
    run_id: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    legal_resolutions: list[str] = Field(default_factory=list)
    created_at: datetime
    resolved_at: datetime | None = None
    resolution: str | None = None

    @model_validator(mode="after")
    def add_agent_binding_retry_for_legacy_blocks(self) -> "PlanBlock":
        """Older agent-capability blocks predate executable registry recovery."""
        if (
            self.kind == "agent_capability"
            and self.active
            and "retry_stage" not in self.legal_resolutions
        ):
            self.legal_resolutions = ["retry_stage", *self.legal_resolutions]
        return self

    @property
    def active(self) -> bool:
        return self.resolved_at is None


class IntentProposal(BaseModel):
    id: str
    kind: ProposalKind
    base_plan_version: int
    source_cycle_id: str | None = None
    objective: str
    scope: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    revision: int = 1
    planner_session_ref: str | None = None
    approved_at: datetime | None = None
    cancelled_at: datetime | None = None


class GoalOutline(BaseModel):
    key: str
    name: str
    objective: str
    position: int
    depends_on: list[str] = Field(default_factory=list)


class CycleDraft(BaseModel):
    id: str
    intent_proposal_id: str
    base_plan_version: int
    source_cycle_id: str | None = None
    goals: list[GoalOutline]
    revision: int = 1
    unfinished_source_treatment: str | None = None
    approved_at: datetime | None = None
    cancelled_at: datetime | None = None

    @model_validator(mode="after")
    def validate_dependencies(self) -> "CycleDraft":
        keys = [goal.key for goal in self.goals]
        if len(keys) != len(set(keys)):
            raise ValueError("cycle draft goal keys must be unique")
        known = set(keys)
        positions = {goal.key: goal.position for goal in self.goals}
        graph: dict[str, list[str]] = {}
        for goal in self.goals:
            if goal.key in goal.depends_on:
                raise ValueError(f"goal '{goal.key}' cannot depend on itself")
            unknown = sorted(set(goal.depends_on) - known)
            if unknown:
                raise ValueError(f"goal '{goal.key}' has unknown dependencies: {unknown}")
            later = sorted(
                dependency
                for dependency in goal.depends_on
                if positions[dependency] >= goal.position
            )
            if later:
                raise ValueError(f"goal '{goal.key}' dependencies must precede it: {later}")
            graph[goal.key] = list(goal.depends_on)

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(key: str) -> None:
            if key in visiting:
                raise ValueError("cycle draft dependencies must be acyclic")
            if key in visited:
                return
            visiting.add(key)
            for dependency in graph[key]:
                visit(dependency)
            visiting.remove(key)
            visited.add(key)

        for key in keys:
            visit(key)
        return self


class Cycle(BaseModel):
    id: str
    intent_proposal_id: str
    draft_id: str
    status: CycleStatus = CycleStatus.ACTIVE
    goals: list[Goal] = Field(default_factory=list)
    started_at: datetime
    completed_at: datetime | None = None
    superseded_at: datetime | None = None
    cancelled_at: datetime | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    output_disposition: OutputDisposition | None = None
    output_reference: str | None = None

    @property
    def immutable(self) -> bool:
        return self.status == CycleStatus.COMPLETED
