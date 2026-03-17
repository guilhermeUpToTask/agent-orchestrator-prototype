"""
src/domain/value_objects/execution.py — Execution-related value objects.

ExecutionContext is the payload sent to an agent session — immutable once
constructed, carries everything the agent needs to know about the task.

AgentExecutionResult is the result returned by the runtime adapter — a
pure data carrier, compared by value, no identity of its own.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from src.domain.value_objects.task import ExecutionSpec


class ExecutionContext(BaseModel):
    """Payload sent to an agent CLI session describing the task."""

    task_id: str
    title: str
    description: str
    execution: ExecutionSpec
    allowed_files: list[str]
    workspace_dir: str
    branch: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentExecutionResult(BaseModel):
    """Result returned by AgentRuntimePort after the agent session completes."""

    success: bool
    exit_code: int
    modified_files: list[str] = Field(default_factory=list)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    elapsed_seconds: float = 0.0
    forbidden_file_violations: list[str] = Field(default_factory=list)
