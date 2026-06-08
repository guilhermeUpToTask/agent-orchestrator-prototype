"""
src/api/sse.py — Server-Sent Events queue and publisher.

All routers call ``publish_sse()`` after mutating state so the frontend
dashboard receives real-time notifications without polling.

The ``event_stream`` route in ``routers/events.py`` drains this queue.
"""
from __future__ import annotations

import asyncio

import structlog

log = structlog.get_logger(__name__)

_sse_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=200)


def publish_sse(event_type: str, payload: dict) -> None:
    """Non-blocking SSE publish.  Drops the event if the queue is full."""
    try:
        _sse_queue.put_nowait({"type": event_type, "payload": payload})
    except asyncio.QueueFull:
        log.warning("api.sse_queue_full", event_type=event_type)


def get_sse_queue() -> asyncio.Queue[dict]:
    """Expose the queue for the event-stream route."""
    return _sse_queue
