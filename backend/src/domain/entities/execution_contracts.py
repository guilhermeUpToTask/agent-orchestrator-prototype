"""Frozen goal/task contracts and bounded deterministic-verification artifacts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator


class VerificationStrategy(str, Enum):
    TDD = "tdd"
    CHARACTERIZATION = "characterization"
    EXECUTABLE_CHECK = "executable_check"


class VerificationKind(str, Enum):
    BASELINE = "baseline"
    RED = "red"
    CHARACTERIZATION = "characterization"
    EXECUTABLE_CHECK = "executable_check"
    AUTHORITATIVE_TEST = "authoritative_test"
    REGRESSION = "regression"
    SCOPE = "scope"
    BRANCH_INTEGRITY = "branch_integrity"
    CLEANUP = "cleanup"


class ContractCriterion(BaseModel):
    id: str
    description: str


class TaskContract(BaseModel):
    id: str
    position: int
    revision: int = 1
    objective: str
    acceptance_criteria: list[ContractCriterion]
    goal_criterion_ids: list[str]
    allowed_scope: list[str]
    forbidden_scope: list[str] = Field(default_factory=list)
    verification_commands: list[str]
    verification_strategy: VerificationStrategy
    required_capabilities: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_contract(self) -> "TaskContract":
        criterion_ids = [criterion.id for criterion in self.acceptance_criteria]
        if not criterion_ids or len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("task criterion ids must be non-empty and unique")
        if not self.goal_criterion_ids:
            raise ValueError("task must map at least one goal criterion")
        if not self.allowed_scope:
            raise ValueError("task allowed scope must be explicit")
        if not self.verification_commands:
            raise ValueError("task requires executable verification commands")
        return self


class GoalContract(BaseModel):
    id: str
    objective: str
    acceptance_criteria: list[ContractCriterion]
    tasks: list[TaskContract]
    cross_task_integration_criterion_ids: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    frozen_at: datetime

    @model_validator(mode="after")
    def validate_coverage(self) -> "GoalContract":
        goal_ids = [criterion.id for criterion in self.acceptance_criteria]
        if not goal_ids or len(goal_ids) != len(set(goal_ids)):
            raise ValueError("goal criterion ids must be non-empty and unique")
        positions = [task.position for task in self.tasks]
        if positions != list(range(len(self.tasks))):
            raise ValueError("tasks must have contiguous stable positions")
        task_ids = [task.id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("task ids must be unique within a goal contract")
        mapped = {criterion for task in self.tasks for criterion in task.goal_criterion_ids}
        unknown = mapped - set(goal_ids)
        missing = set(goal_ids) - mapped
        if unknown:
            raise ValueError(f"unknown goal criterion mappings: {sorted(unknown)}")
        if missing:
            raise ValueError(f"uncovered goal criteria: {sorted(missing)}")
        integration = set(self.cross_task_integration_criterion_ids)
        if not integration.issubset(set(goal_ids)):
            raise ValueError("integration criteria must reference goal criteria")
        if integration:
            final = self.tasks[-1]
            if not integration.issubset(set(final.goal_criterion_ids)):
                raise ValueError(
                    "cross-task criteria must be covered by the final integration task"
                )
        return self


class TestBundleState(str, Enum):
    FROZEN = "frozen"
    INVALID = "invalid"


class TestBundle(BaseModel):
    task_id: str
    task_revision: int
    test_commit_sha: str
    protected_file_hashes: dict[str, str]
    criterion_to_tests: dict[str, list[str]]
    verification_strategy: VerificationStrategy
    baseline_evidence_refs: list[str] = Field(default_factory=list)
    red_or_baseline_evidence_refs: list[str]
    state: TestBundleState = TestBundleState.FROZEN
    frozen_at: datetime
    invalidated_at: datetime | None = None
    invalidation_reason: str | None = None

    def invalidate(self, reason: str, at: datetime | None = None) -> None:
        if self.state == TestBundleState.INVALID:
            return
        self.state = TestBundleState.INVALID
        self.invalidated_at = at
        self.invalidation_reason = reason

    def validates(self, task_id: str, revision: int) -> bool:
        return (
            self.state == TestBundleState.FROZEN
            and self.task_id == task_id
            and self.task_revision == revision
        )


class VerificationEvidence(BaseModel):
    id: str
    task_id: str
    task_revision: int
    run_id: str
    candidate_commit_sha: str
    test_commit_sha: str
    exact_command: str
    exit_code: int
    started_at: datetime
    finished_at: datetime
    bounded_output_ref: str
    verification_kind: VerificationKind
    accepted: bool

    @model_validator(mode="after")
    def validate_time(self) -> "VerificationEvidence":
        if self.finished_at < self.started_at:
            raise ValueError("verification finish precedes start")
        return self


class CycleEvidence(BaseModel):
    cycle_id: str
    commit_sha: str
    verification_evidence_refs: list[str]
    commands: list[str]
    accepted_at: datetime
