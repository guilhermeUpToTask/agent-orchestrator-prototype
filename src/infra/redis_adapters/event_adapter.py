"""
src/infra/redis_adapters/event_adapter.py — Redis Streams EventPort adapter.

Events are published to stream  events:{event_type}
and to a global stream        events:all

Consumers read with XREADGROUP so each message is delivered to exactly
one consumer in a group, preventing duplicate processing across multiple
workers or task manager instances.
"""
from __future__ import annotations

import json
from typing import Iterator

import redis

from src.core.models import DomainEvent
from src.core.ports import EventPort


_GLOBAL_STREAM = "events:all"


class RedisEventAdapter(EventPort):

    def __init__(self, redis_client: redis.Redis, journal_dir: str = "workflow/events") -> None:
        self._r = redis_client
        import pathlib
        self._journal_dir = pathlib.Path(journal_dir)
        self._journal_dir.mkdir(parents=True, exist_ok=True)

    def publish(self, event: DomainEvent) -> None:
        payload = event.model_dump(mode="json")
        serialized = json.dumps(payload, default=str)
        pipe = self._r.pipeline()
        pipe.xadd(f"events:{event.type}", {"data": serialized})
        pipe.xadd(_GLOBAL_STREAM, {"data": serialized})
        pipe.execute()
        self._write_journal(event)

    def subscribe(self, event_type: str, group: str, consumer: str) -> Iterator[DomainEvent]:
        """Subscribe to a single event type. See subscribe_many() for multiple types."""
        yield from self.subscribe_many([event_type], group, consumer)

    def subscribe_many(self, event_types: list[str], group: str, consumer: str) -> Iterator[DomainEvent]:
        """
        Block-subscribe to multiple event types in a single XREADGROUP call.
        Yields events from any of the given streams as they arrive — no
        starvation, no missed types.

        Args:
            event_types: e.g. ["task.created", "task.requeued", "task.completed"]
            group:       consumer group name, e.g. "task-manager" or "workers"
            consumer:    unique consumer name within the group, e.g. AGENT_ID
        """
        streams = {f"events:{et}": et for et in event_types}

        # Create consumer groups for all streams (idempotent).
        # id="0" — start from the beginning of the stream so events published
        # before this group existed (startup race, Redis restart) are not
        # permanently lost.  XREADGROUP with ">" only delivers messages that
        # have not yet been ACKed by anyone in the group, so already-processed
        # events are not replayed.  id="$" (start from now) would silently
        # discard any event that arrived in the gap between process start and
        # the first xgroup_create call.
        for stream_key in streams:
            try:
                self._r.xgroup_create(stream_key, group, id="0", mkstream=True)
            except redis.exceptions.ResponseError:
                pass  # group already exists — resume from its current position

        # Build the read dict: {stream_key: ">"} for all streams.
        read_dict = {key: ">" for key in streams}

        while True:
            results = self._r.xreadgroup(
                group, consumer,
                read_dict,
                block=5000,
                count=10,   # read up to 10 messages across all streams per call
            )
            if not results:
                continue
            for stream_key, messages in results:
                stream_name = stream_key.decode() if isinstance(stream_key, bytes) else stream_key
                for msg_id, fields in messages:
                    try:
                        data_raw = fields.get(b"data") or fields.get("data", b"{}")
                        if isinstance(data_raw, bytes):
                            data_raw = data_raw.decode()
                        data = json.loads(data_raw)
                        yield DomainEvent.model_validate(data)
                    finally:
                        # Always ack so Redis doesn't redeliver on reconnect.
                        # Recovery on processing failure is handled by the reconciler.
                        self._r.xack(stream_name, group, msg_id)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _write_journal(self, event: DomainEvent) -> None:
        try:
            path = self._journal_dir / f"evt-{event.timestamp.strftime('%Y%m%dT%H%M%S')}-{event.event_id[:8]}.json"
            path.write_text(json.dumps(event.model_dump(mode="json"), indent=2, default=str))
        except Exception:
            pass  # journal is optional


# ---------------------------------------------------------------------------
# In-memory event adapter (for testing / dry-run)
# ---------------------------------------------------------------------------

class InMemoryEventAdapter(EventPort):
    """Simple in-process pub/sub for testing. Not thread-safe."""

    def __init__(self) -> None:
        self._published: list[DomainEvent] = []
        self._subscribers: dict[str, list[DomainEvent]] = {}

    def publish(self, event: DomainEvent) -> None:
        self._published.append(event)
        self._subscribers.setdefault(event.type, []).append(event)

    def subscribe(self, event_type: str, group: str = "", consumer: str = "") -> Iterator[DomainEvent]:
        yield from self._subscribers.get(event_type, [])

    def subscribe_many(self, event_types: list[str], group: str = "", consumer: str = "") -> Iterator[DomainEvent]:
        for et in event_types:
            yield from self._subscribers.get(et, [])

    @property
    def all_events(self) -> list[DomainEvent]:
        return list(self._published)

    def events_of_type(self, event_type: str) -> list[DomainEvent]:
        return [e for e in self._published if e.type == event_type]