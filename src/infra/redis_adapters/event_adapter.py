"""
src/infra/redis_adapters/event_adapter.py — Redis Streams EventPort adapter.

Events are published to stream  events:{event_type}
and to a global stream        events:all

Consumers read with XREAD (blocking).
"""
from __future__ import annotations

import json
from typing import Iterator

import redis

from src.core.models import DomainEvent
from src.core.ports import EventPort


_GLOBAL_STREAM = "events:all"
_JOURNAL_KEY = "events:journal"


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
        # Per-type stream
        pipe.xadd(f"events:{event.type}", {"data": serialized})
        # Global stream
        pipe.xadd(_GLOBAL_STREAM, {"data": serialized})
        pipe.execute()
        # Optional: write to filesystem journal
        self._write_journal(event)

    def subscribe(self, event_type: str) -> Iterator[DomainEvent]:
        stream_key = f"events:{event_type}"
        last_id = "0"
        while True:
            results = self._r.xread({stream_key: last_id}, block=5000, count=10)
            if not results:
                continue
            for _stream, messages in results:
                for msg_id, fields in messages:
                    last_id = msg_id.decode() if isinstance(msg_id, bytes) else msg_id
                    data_raw = fields.get(b"data") or fields.get("data", b"{}")
                    if isinstance(data_raw, bytes):
                        data_raw = data_raw.decode()
                    data = json.loads(data_raw)
                    yield DomainEvent.model_validate(data)

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

    def subscribe(self, event_type: str) -> Iterator[DomainEvent]:
        yield from self._subscribers.get(event_type, [])

    @property
    def all_events(self) -> list[DomainEvent]:
        return list(self._published)

    def events_of_type(self, event_type: str) -> list[DomainEvent]:
        return [e for e in self._published if e.type == event_type]
