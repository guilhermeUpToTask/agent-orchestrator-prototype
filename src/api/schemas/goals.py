"""src/api/schemas/goals.py — Goal-related API DTOs."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from src.domain.aggregates.goal import GoalStatus
from src.domain.value_objects.status import TaskStatus


class GoalTaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    task_id: str
    title: str
    status: TaskStatus
    depends_on: list[str]
    # Enriched from the task repository when available
    assigned_agent_id: Optional[str] = None
    retry_count: int = 0
    # Set when no active agent matches the task's required capability.
    unassignable_reason: Optional[str] = None
    # The failure reason from the last execution attempt (git error, test fail…).
    last_error: Optional[str] = None


class GoalHistoryEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event: str
    timestamp: Optional[datetime] = None
    actor: Optional[str] = None
    detail: Optional[dict[str, Any]] = None


class GoalResponse(BaseModel):
    """Full goal read-model."""
    goal_id: str
    name: str
    description: str
    status: GoalStatus
    feature_tag: Optional[str] = None
    depends_on: list[str]
    tasks: list[GoalTaskResponse]
    history: list[GoalHistoryEntryResponse]
    # GitHub PR gate state (None until a PR is opened for the goal branch)
    pr_number: Optional[int] = None
    pr_status: Optional[str] = None  # "open" | "closed" | "merged"
    pr_html_url: Optional[str] = None
    pr_checks_passed: bool = False
    pr_approved: bool = False


# ── Finalize ──────────────────────────────────────────────────────────────────

class GoalFinalizeResponse(BaseModel):
    goal_id: str
    pr_number: Optional[int]
    pr_url: Optional[str]
    goal_status: str


# ── Retry ─────────────────────────────────────────────────────────────────────

class GoalRetryResponse(BaseModel):
    """Result of a bulk 'retry failed tasks' action."""
    requeued: list[str]       # task_ids that were force-requeued
    goals_touched: list[str]  # goal_ids that had at least one requeue
