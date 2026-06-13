"""
src/api/event_bridge.py — Redis events:all → SSE bridge.

Runs as a lifespan thread in the API process. Reads the global domain-event
stream with plain XREAD (no group, no ack — every API instance observes
everything, which is exactly right for UI fan-out) and translates domain
events into the SSE vocabulary the frontend already handles.

This is what makes the canvas update live: workers, the task manager, the
goal orchestrator and the reconciler all publish domain events from outside
any HTTP request, and before this bridge existed none of them ever reached
the frontend.
"""
from __future__ import annotations

import json
import time
from typing import Callable

import structlog

from src.api.sse import SSEBroker

log = structlog.get_logger(__name__)

_GLOBAL_STREAM = "events:all"
_BLOCK_MS = 1000
_RECONNECT_BACKOFF_SECONDS = 5.0

# Domain task lifecycle → the single task.status_changed SSE event the
# frontend switches on (frontend/src/lib/queries.ts).
_TASK_STATUS_BY_EVENT = {
    "task.created": "created",
    "task.assigned": "assigned",
    "task.started": "in_progress",
    "task.completed": "succeeded",
    "task.failed": "failed",
    "task.requeued": "requeued",
    "task.canceled": "canceled",
}

# Worker progress events are internal protocol between worker and task
# manager; the resulting state change reaches the UI via task.started/
# completed/failed once the task manager persists it.
_INTERNAL_EVENTS = {
    "task.execution_started",
    "task.execution_succeeded",
    "task.execution_failed",
}


def map_domain_event_to_sse(event_type: str, payload: dict) -> tuple[str, dict] | None:
    """Translate one domain event into (sse_type, sse_payload), or None to drop."""
    if event_type in _INTERNAL_EVENTS:
        return None
    status = _TASK_STATUS_BY_EVENT.get(event_type)
    if status is not None:
        return (
            "task.status_changed",
            {"task_id": payload.get("task_id"), "status": status},
        )
    if event_type == "goal.pr_opened":
        return (
            "goal.pr_opened",
            {"goal_id": payload.get("goal_id"), "pr_number": payload.get("pr_number")},
        )
    if event_type.startswith("goal."):
        # ready_for_review / approved / merged / unblocked / pr_state_synced …
        # → the frontend treats goal events as "refetch the goals cache".
        return ("goal.pr_state_synced", {"goal_id": payload.get("goal_id")})
    # plan.* and anything unknown: forward as-is; the frontend's default
    # branch invalidates conservatively.
    return (event_type, payload)


def run_event_bridge(redis_client, broker: SSEBroker, stop: Callable[[], bool]) -> None:
    """Thread target: pump events:all into the SSE broker until stop()."""
    last_id = "$"  # only events published after the bridge starts
    log.info("event_bridge.started", stream=_GLOBAL_STREAM)

    while not stop():
        try:
            results = redis_client.xread({_GLOBAL_STREAM: last_id}, block=_BLOCK_MS, count=50)
        except Exception as exc:
            log.warning("event_bridge.read_failed", error=str(exc))
            # Re-resolve from the stream tail after the backoff: missing a
            # few events is fine (staleTime refetch is the safety net),
            # re-reading from a stale id after an outage is not.
            last_id = "$"
            time.sleep(_RECONNECT_BACKOFF_SECONDS)
            continue

        for _stream, messages in results or []:
            for msg_id, fields in messages:
                last_id = msg_id.decode() if isinstance(msg_id, bytes) else msg_id
                raw = fields.get(b"data") or fields.get("data", b"{}")
                if isinstance(raw, bytes):
                    raw = raw.decode()
                try:
                    data = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                mapped = map_domain_event_to_sse(
                    data.get("type", ""), data.get("payload") or {}
                )
                if mapped is not None:
                    broker.publish(mapped[0], mapped[1])

    log.info("event_bridge.stopped")
