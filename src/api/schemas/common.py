"""src/api/schemas/common.py — Shared primitive DTOs."""
from __future__ import annotations

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    detail: str


class PlanConflictResponse(BaseModel):
    """409 body for plan lifecycle conflicts — tells the client exactly
    which transition was attempted and what the plan state actually is."""

    detail: str
    action: str
    current_status: str
    expected_status: list[str]


class HealthResponse(BaseModel):
    status: str
    version: str
