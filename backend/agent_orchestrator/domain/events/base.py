from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field


class DomainEvent(BaseModel):
    """Base for COARSE domain events written to the outbox transactionally with
    state. event_id is the dedup key consumers use (at-least-once delivery)."""

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # aggregate/stream key: every coarse event belongs to exactly one plan; consumers
    # route/partition by it and use it to fetch full state (payloads stay id-only).
    plan_id: str

    # stable class-name discriminator used for (de)serialization and consumer routing.
    @property
    def event_type(self) -> str:
        return type(self).__name__
