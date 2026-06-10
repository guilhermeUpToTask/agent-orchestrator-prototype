"""src/api/schemas/goals.py — Goal-related API DTOs."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class GoalTaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    task_id: str
    title: str
    status: str
    depends_on: list[str]


class GoalHistoryEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event: str
    timestamp: Optional[str] = None
    actor: Optional[str] = None
    detail: Optional[dict[str, Any]] = None


class GoalResponse(BaseModel):
    """Full goal read-model."""
    goal_id: str
    name: str
    description: str
    status: str
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
