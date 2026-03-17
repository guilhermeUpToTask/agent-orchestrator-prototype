"""
src/domain/value_objects/task.py — Task value objects.

Value objects are immutable descriptors. They own validation and small
domain behaviours but hold no mutable identity of their own.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field

from src.domain.errors import ForbiddenFileEditError


class AgentSelector(BaseModel):
    """Declares what kind of agent a task requires."""
    required_capability: str
    min_version: str = ">=1.0.0"


class ExecutionSpec(BaseModel):
    """Specifies how the task should be executed and what constraints apply."""
    type: str
    constraints: dict[str, Any] = Field(default_factory=dict)
    acceptance_criteria: list[str] = Field(default_factory=list)
    files_allowed_to_modify: list[str] = Field(default_factory=list)
    test_command: Optional[str] = None

    def validate_modifications(self, modified_files: list[str]) -> None:
        """Raise ForbiddenFileEditError listing every file outside the allowed set."""
        allowed = set(self.files_allowed_to_modify)
        violations = [f for f in modified_files if f not in allowed]
        if violations:
            raise ForbiddenFileEditError(violations)


class RetryPolicy(BaseModel):
    """Tracks retry attempts and enforces the retry budget."""
    max_retries: int = 2
    backoff_seconds: int = 30
    attempt: int = 0

    def can_retry(self) -> bool:
        """Return True if at least one retry attempt remains."""
        return self.attempt < self.max_retries

    def increment(self, task_id: str = "<unknown>") -> None:
        """Consume one retry attempt. Raises MaxRetriesExceededError if exhausted."""
        from src.domain.errors import MaxRetriesExceededError
        if not self.can_retry():
            raise MaxRetriesExceededError(task_id, self.attempt, self.max_retries)
        self.attempt += 1


class Assignment(BaseModel):
    """Records which agent was assigned and under what lease terms."""
    agent_id: str
    assigned_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    lease_seconds: int = 300
    lease_token: Optional[str] = None


class HistoryEntry(BaseModel):
    """Immutable audit log entry appended on every state transition."""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event: str
    actor: str
    detail: dict[str, Any] = Field(default_factory=dict)


class TaskResult(BaseModel):
    """The output produced by a successful agent execution."""
    branch: Optional[str] = None
    commit_sha: Optional[str] = None
    artifacts: dict[str, Any] = Field(default_factory=dict)
    modified_files: list[str] = Field(default_factory=list)
