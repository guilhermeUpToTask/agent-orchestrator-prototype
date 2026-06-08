"""src/api/schemas/tasks.py — Task-related API DTOs."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


# ── Task Retry ────────────────────────────────────────────────────────────────

class TaskRetryRequest(BaseModel):
    actor: str = Field(default="api:task-retry", max_length=100)


class TaskRetryResponse(BaseModel):
    task_id: str
    previous_status: str


# ── Task Delete ───────────────────────────────────────────────────────────────

class TaskDeleteResponse(BaseModel):
    task_id: str
    previous_status: str


# ── Task Prune ────────────────────────────────────────────────────────────────

class TaskPruneRequest(BaseModel):
    filter_statuses: Optional[list[str]] = Field(
        default=None,
        description=(
            "List of task statuses to target. "
            "When omitted, ALL tasks are pruned."
        ),
    )


class TaskPruneResponse(BaseModel):
    deleted: list[str]
    count: int


# ── Task Assign ───────────────────────────────────────────────────────────────

class TaskAssignResponse(BaseModel):
    task_id: str
    outcome: str


# ── Task Unblock ──────────────────────────────────────────────────────────────

class TaskUnblockResponse(BaseModel):
    completed_task_id: str
    unblocked: list[str]
    skipped: list[str]
    count: int


# ── Task Fail Handling ────────────────────────────────────────────────────────

class TaskFailHandlingResponse(BaseModel):
    task_id: str
    outcome: str
