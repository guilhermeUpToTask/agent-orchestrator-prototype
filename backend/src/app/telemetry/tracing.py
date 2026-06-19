from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import uuid4


@dataclass(frozen=True)
class TraceContext:
    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None
    correlation_id: Optional[str] = None
    causation_id: Optional[str] = None


def _new_id() -> str:
    return uuid4().hex


def start_trace(goal_id: str | None = None, correlation_id: str | None = None) -> TraceContext:
    trace_id = _new_id()
    root_span = _new_id()
    corr = correlation_id or goal_id or trace_id
    return TraceContext(
        trace_id=trace_id,
        span_id=root_span,
        parent_span_id=None,
        correlation_id=corr,
        causation_id=None,
    )


def start_span(parent_context: TraceContext) -> TraceContext:
    return TraceContext(
        trace_id=parent_context.trace_id,
        span_id=_new_id(),
        parent_span_id=parent_context.span_id,
        correlation_id=parent_context.correlation_id,
        causation_id=parent_context.span_id,
    )
