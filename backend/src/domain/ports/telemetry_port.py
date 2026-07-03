"""The telemetry port: fine-grained agent runtime events.

Best-effort by contract — a lost telemetry event never loses state (the
transactional outbox carries the coarse domain events; this stream is the
live agent feed).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.domain.events.agent_events import AgentEvent


@runtime_checkable
class AgentEventSink(Protocol):
    """Best-effort telemetry sink for fine-grained agent runtime events."""

    async def emit(self, event: AgentEvent) -> None: ...
