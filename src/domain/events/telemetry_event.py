from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class TelemetryEvent(BaseModel):
    """Infrastructure-agnostic telemetry envelope for orchestration traces."""

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    event_type: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None

    correlation_id: Optional[str] = None
    causation_id: Optional[str] = None

    producer: str
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    goal_id: Optional[str] = None
    task_id: Optional[str] = None
    agent_id: Optional[str] = None
    agent_version: Optional[str] = None
    prompt_version: Optional[str] = None
