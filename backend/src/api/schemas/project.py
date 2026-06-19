"""src/api/schemas/project.py — Project-level operation DTOs."""
from __future__ import annotations

from pydantic import BaseModel


class ProjectContextResponse(BaseModel):
    project_name: str
    mode: str


class ProjectResetRequest(BaseModel):
    keep_agents: bool = False


class ProjectResetResponse(BaseModel):
    tasks_deleted: int
    leases_released: int
    branches_deleted: int
    agents_removed: int
    had_errors: bool
    errors: list[str]
