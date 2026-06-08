"""src/api/schemas/discovery.py — Discovery session API DTOs."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class DiscoveryStartResponse(BaseModel):
    """Returned by POST /discovery/start — either a question or completion."""
    question: Optional[str] = None
    done: bool
    brief: Optional[dict[str, Any]] = None


class DiscoveryMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4096)


class DiscoveryMessageResponse(BaseModel):
    question: Optional[str]
    done: bool
    brief: Optional[dict[str, Any]] = None
