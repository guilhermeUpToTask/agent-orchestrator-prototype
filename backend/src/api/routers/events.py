"""
/api/events — the live SSE stream.

Each client gets its own broker queue (fan-out, not steal). Everything the
outbox relay delivers arrives here: coarse domain events (system feed) and
agent runtime events ("agent.event", the live agent feed). Consumers dedup on
the event_id in every payload — delivery is at-least-once.
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from src.api.sse import get_broker

router = APIRouter(tags=["events"])

_KEEPALIVE_SECONDS = 15.0


@router.get("/events")
async def stream_events(request: Request) -> StreamingResponse:
    broker = get_broker()
    queue = broker.register()

    async def gen() -> AsyncIterator[str]:
        try:
            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=_KEEPALIVE_SECONDS)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield (f"event: {event['type']}\ndata: {json.dumps(event['payload'])}\n\n")
        finally:
            broker.unregister(queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
