from __future__ import annotations

from typing import Any

from src.app.telemetry.tracing import TraceContext, start_span, start_trace
from src.domain import TelemetryEmitterPort, TelemetryEvent


class TelemetryService:
    def __init__(self, emitter: TelemetryEmitterPort | None, producer: str) -> None:
        self._emitter = emitter
        self._producer = producer

    def start_trace(self, goal_id: str | None = None, correlation_id: str | None = None) -> TraceContext:
        return start_trace(goal_id=goal_id, correlation_id=correlation_id)

    def start_span(self, parent_context: TraceContext) -> TraceContext:
        return start_span(parent_context)

    def emit(
        self,
        event_type: str,
        context: TraceContext,
        payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        *,
        goal_id: str | None = None,
        task_id: str | None = None,
        agent_id: str | None = None,
        agent_version: str | None = None,
        prompt_version: str | None = None,
    ) -> None:
        if self._emitter is None:
            return
        event = TelemetryEvent(
            event_type=event_type,
            trace_id=context.trace_id,
            span_id=context.span_id,
            parent_span_id=context.parent_span_id,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
            producer=self._producer,
            payload=payload or {},
            metadata=metadata or {},
            goal_id=goal_id,
            task_id=task_id,
            agent_id=agent_id,
            agent_version=agent_version,
            prompt_version=prompt_version,
        )
        self._emitter.emit(event)
