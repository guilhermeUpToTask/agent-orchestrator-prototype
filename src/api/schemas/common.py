"""src/api/schemas/common.py — Shared primitive DTOs."""
from __future__ import annotations

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    detail: str


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorEnvelope(BaseModel):
    """Consistent error body for the control-plane endpoints.

    Stack traces are never exposed here; they are logged internally only. The
    ``request_id`` lets an operator correlate a client error with server logs.
    """

    error: ErrorDetail


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
