"""
src/core/models.py — Domain models (Pydantic v2).
All domain state lives here. No infrastructure imports.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class TaskStatus(str, Enum):
    CREATED = "created"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    REQUEUED = "requeued"
    MERGED = "merged"


class TrustLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

class AgentProps(BaseModel):
    agent_id: str
    name: str
    capabilities: list[str] = Field(default_factory=list)
    version: str = "1.0.0"
    tools: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    endpoint: Optional[str] = None
    last_heartbeat: Optional[datetime] = None
    max_concurrent_tasks: int = 1
    trust_level: TrustLevel = TrustLevel.MEDIUM
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Whether this agent should be started and assigned tasks.
    # Set to False to disable an agent without removing it from the registry.
    active: bool = True
    # Which runtime adapter to use for this agent.
    # "gemini" | "claude" | "dry-run"
    runtime_type: str = "gemini"
    # Runtime-specific config passed through to the adapter (model, flags, etc.)
    runtime_config: dict[str, Any] = Field(default_factory=dict)


class Assignment(BaseModel):
    agent_id: str
    assigned_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    lease_seconds: int = 300
    lease_token: Optional[str] = None


class ExecutionSpec(BaseModel):
    type: str  # e.g. "code:backend"
    constraints: dict[str, Any] = Field(default_factory=dict)
    acceptance_criteria: list[str] = Field(default_factory=list)
    files_allowed_to_modify: list[str] = Field(default_factory=list)
    test_command: Optional[str] = None


class RetryPolicy(BaseModel):
    max_retries: int = 2
    backoff_seconds: int = 30
    attempt: int = 0


class HistoryEntry(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event: str
    actor: str
    detail: dict[str, Any] = Field(default_factory=dict)


class TaskResult(BaseModel):
    branch: Optional[str] = None
    commit_sha: Optional[str] = None
    artifacts: dict[str, Any] = Field(default_factory=dict)
    modified_files: list[str] = Field(default_factory=list)


class AgentSelector(BaseModel):
    required_capability: str
    min_version: str = ">=1.0.0"


# ---------------------------------------------------------------------------
# TaskAggregate — authoritative domain entity
# ---------------------------------------------------------------------------

class TaskAggregate(BaseModel):
    task_id: str
    feature_id: str
    title: str
    description: str
    agent_selector: AgentSelector
    execution: ExecutionSpec
    status: TaskStatus = TaskStatus.CREATED
    assignment: Optional[Assignment] = None
    state_version: int = 1
    history: list[HistoryEntry] = Field(default_factory=list)
    result: Optional[TaskResult] = None
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # Tasks this one depends on. Will not be dispatched until all are SUCCEEDED.
    depends_on: list[str] = Field(default_factory=list)

    # ------------------------------------------------------------------
    # Invariant helpers
    # ------------------------------------------------------------------

    def _assert_status(self, *allowed: TaskStatus) -> None:
        if self.status not in allowed:
            raise ValueError(
                f"Task {self.task_id}: expected status in {[s.value for s in allowed]}, "
                f"got '{self.status.value}'"
            )

    def _bump(self, event: str, actor: str, detail: dict[str, Any] | None = None) -> None:
        self.state_version += 1
        self.updated_at = datetime.now(timezone.utc)
        self.history.append(
            HistoryEntry(event=event, actor=actor, detail=detail or {})
        )

    # ------------------------------------------------------------------
    # State transitions (enforce invariants, bump version, append history)
    # ------------------------------------------------------------------

    def assign(self, assignment: Assignment) -> "TaskAggregate":
        self._assert_status(TaskStatus.CREATED, TaskStatus.REQUEUED)
        self.assignment = assignment
        self.status = TaskStatus.ASSIGNED
        self._bump("task.assigned", assignment.agent_id, {"lease_seconds": assignment.lease_seconds})
        return self

    def start(self) -> "TaskAggregate":
        self._assert_status(TaskStatus.ASSIGNED)
        self.status = TaskStatus.IN_PROGRESS
        self._bump("task.started", self.assignment.agent_id if self.assignment else "unknown")
        return self

    def complete(self, result: TaskResult) -> "TaskAggregate":
        self._assert_status(TaskStatus.IN_PROGRESS)
        self.result = result
        self.status = TaskStatus.SUCCEEDED
        self._bump("task.completed", self.assignment.agent_id if self.assignment else "unknown",
                   {"commit_sha": result.commit_sha, "branch": result.branch})
        return self

    def fail(self, reason: str) -> "TaskAggregate":
        self._assert_status(TaskStatus.IN_PROGRESS, TaskStatus.ASSIGNED)
        self.status = TaskStatus.FAILED
        self._bump("task.failed",
                   self.assignment.agent_id if self.assignment else "system",
                   {"reason": reason})
        return self

    def requeue(self) -> "TaskAggregate":
        self._assert_status(TaskStatus.FAILED)
        if self.retry_policy.attempt >= self.retry_policy.max_retries:
            raise ValueError(
                f"Task {self.task_id} exceeded max retries "
                f"({self.retry_policy.max_retries})"
            )
        self.retry_policy.attempt += 1
        self.assignment = None
        self.status = TaskStatus.REQUEUED
        self._bump("task.requeued", "reconciler",
                   {"attempt": self.retry_policy.attempt})
        return self

    def cancel(self, reason: str = "") -> "TaskAggregate":
        self.status = TaskStatus.CANCELED
        self._bump("task.canceled", "system", {"reason": reason})
        return self

    def mark_merged(self) -> "TaskAggregate":
        self._assert_status(TaskStatus.SUCCEEDED)
        self.status = TaskStatus.MERGED
        self._bump("task.merged", "system")
        return self


# ---------------------------------------------------------------------------
# Domain events (minimal payload — IDs only)
# ---------------------------------------------------------------------------

class DomainEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    type: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    correlation_id: Optional[str] = None
    causation_id: Optional[str] = None
    producer: str
    payload: dict[str, Any]           # MUST be minimal — IDs only
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Execution context (sent to agent CLI session)
# ---------------------------------------------------------------------------

class ExecutionContext(BaseModel):
    task_id: str
    title: str
    description: str
    execution: ExecutionSpec
    allowed_files: list[str]
    workspace_dir: str
    branch: str
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Agent execution result (returned by AgentRuntimePort)
# ---------------------------------------------------------------------------

class AgentExecutionResult(BaseModel):
    success: bool
    exit_code: int
    modified_files: list[str] = Field(default_factory=list)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    elapsed_seconds: float = 0.0
    forbidden_file_violations: list[str] = Field(default_factory=list)