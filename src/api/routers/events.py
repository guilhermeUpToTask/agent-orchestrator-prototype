"""
src/api/routers/events.py — Server-Sent Events streaming endpoint.

Covers:
  GET /events    long-lived SSE stream for real-time plan/goal/task updates
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from src.api.sse import get_sse_queue

router = APIRouter(tags=["events"])


@router.get(
    "/events",
    summary="Real-Time Event Stream",
    description=(
        "Opens a `text/event-stream` connection that delivers real-time "
        "domain events to the frontend. Events are JSON objects with `type` "
        "and `payload` keys. A `: ping` keep-alive comment is sent every 25 s "
        "when the queue is idle. Reconnect timeout is set to 3 000 ms."
    ),
)
async def event_stream(request: Request) -> StreamingResponse:
    sse_queue = get_sse_queue()

    async def generator():
        yield "retry: 3000\n\n"
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(sse_queue.get(), timeout=25.0)
                data = json.dumps(event)
                yield f"data: {data}\n\n"
            except asyncio.TimeoutError:
                yield ": ping\n\n"

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
