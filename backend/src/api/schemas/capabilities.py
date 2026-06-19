"""src/api/schemas/capabilities.py — Capability registry API DTOs."""
from __future__ import annotations

from pydantic import BaseModel, Field


class CapabilityListResponse(BaseModel):
    """The full set of registered capability tags."""
    tags: list[str]


class CapabilityCreateRequest(BaseModel):
    tag: str = Field(max_length=100, description="e.g. 'code:backend', 'test:write'")
