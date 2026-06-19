"""
src/domain/events/domain_event.py — DomainEvent.

Domain events are the primary communication mechanism between aggregates
and across bounded contexts. They carry only minimal payload (IDs only) —
consumers load full state from the repository when they need it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class DomainEvent(BaseModel):
    """
    An immutable record that something meaningful happened in the domain.

    Payload rule: IDs only. Never embed full aggregate state in the payload.
    Consumers who need full state call the repository with the task_id.
    """

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    type: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    correlation_id: Optional[str] = None
    causation_id: Optional[str] = None
    producer: str
    payload: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)
