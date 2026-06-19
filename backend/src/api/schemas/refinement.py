"""src/api/schemas/refinement.py — Refinement-related API DTOs."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class RefineRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4096)
    focused_node_id: Optional[str] = None
    focused_goal_id: Optional[str] = None


class RefineResponse(BaseModel):
    session_id: str
    actions_taken: list[str]
    succeeded: bool
    error: Optional[str] = None
