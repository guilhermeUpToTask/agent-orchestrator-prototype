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
import time
from typing import Callable, Iterator, Optional

import redis

from src.domain import DomainEvent
from src.domain import EventPort


_GLOBAL_STREAM = "events:all"

# Default idle threshold before a pending message is claimed from another
# (presumed dead) consumer in the same group. Must exceed the longest
# legitimate processing time in the group — for workers that means
# task_timeout — or a slow-but-alive consumer's message gets double-processed.
_DEFAULT_CLAIM_IDLE_MS = 300_000


class RedisEventAdapter(EventPort):
    def __init__(
        self,
        redis_client: redis.Redis,
        journal_dir: str,
        claim_idle_ms: int | None = _DEFAULT_CLAIM_IDLE_MS,
    ) -> None:
        import pathlib
        self._r = redis_client
        self._journal_dir = pathlib.Path(journal_dir)
        self._journal_dir.mkdir(parents=True, exist_ok=True)
        self._pending: dict[str, tuple[str, str, str]] = {}
        self._claim_idle_ms = claim_idle_ms

    def publish(self, event: DomainEvent) -> None:
        payload = event.model_dump(mode="json")
        serialized = json.dumps(payload, default=str)
        pipe = self._r.pipeline()
        pipe.xadd(f"events:{event.type}", {"data": serialized})
        pipe.xadd(_GLOBAL_STREAM, {"data": serialized})
        pipe.execute()
        self._write_journal(event)

    def subscribe(
        self,
        event_type: str,
        group: str,
        consumer: str,
        stop: Optional[Callable[[], bool]] = None,
    ) -> Iterator[DomainEvent]:
        """Subscribe to a single event type. See subscribe_many() for multiple types."""
        yield from self.subscribe_many([event_type], group, consumer, stop=stop)

    def subscribe_many(
        self,
        event_types: list[str],
        group: str,
        consumer: str,
        stop: Optional[Callable[[], bool]] = None,
    ) -> Iterator[DomainEvent]:
        """
        Block-subscribe to multiple event types in a single XREADGROUP call.
        Yields events from any of the given streams as they arrive — no
        starvation, no missed types.

        Args:
            event_types: e.g. ["task.created", "task.requeued", "task.completed"]
            group:       consumer group name, e.g. "task-manager" or "worker-{agent_id}"
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

        # Recovery phase: claim every message left pending in this group from
        # a previous crash (delivered but never ACKed). All groups in this
        # system are single-consumer, so claiming the whole group PEL at
        # startup is equivalent to replaying our own — and also rescues
        # messages stranded under a renamed/replaced consumer name.
        yield from self._autoclaim(streams, group, consumer, min_idle_ms=0)

        # Build the read dict: {stream_key: ">"} for all streams.
        read_dict = {key: ">" for key in streams}
        last_claim = time.monotonic()
        # Short block so an embedded loop notices stop() within ~1s.
        block_ms = 1000 if stop is not None else 5000

        while True:
            if stop is not None and stop():
                return
            results = self._r.xreadgroup(
                group,
                consumer,
                read_dict,
                block=block_ms,
                count=10,  # read up to 10 messages across all streams per call
            )
            # redis-py's sync client types xreadgroup as Awaitable[T] | T (incl.
            # str), so mypy can't see this as iterable pairs — same Awaitable-union
            # fallout the module override documents.
            for stream_key, messages in results or []:  # type: ignore[assignment, misc]
                yield from self._decode_messages(stream_key, messages, group)

            # Periodically claim messages stuck pending on other consumers in
            # this group (consumer renamed/replaced and never came back).
            if (
                self._claim_idle_ms is not None
                and time.monotonic() - last_claim >= self._claim_idle_ms / 1000
            ):
                last_claim = time.monotonic()
                yield from self._autoclaim(
                    streams, group, consumer, min_idle_ms=self._claim_idle_ms
                )

    def ack(self, event: DomainEvent, group: str) -> None:
        pending = self._pending.get(event.event_id)
        if pending is None:
            return
        stream_name, pending_group, msg_id = pending
        if pending_group != group:
            return
        self._r.xack(stream_name, group, msg_id)
        self._pending.pop(event.event_id, None)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _decode_messages(
        self, stream_key, messages, group: str
    ) -> Iterator[DomainEvent]:
        stream_name = stream_key.decode() if isinstance(stream_key, bytes) else stream_key
        for msg_id, fields in messages:
            if fields is None:
                continue  # XAUTOCLAIM tombstone for a trimmed entry
            data_raw = fields.get(b"data") or fields.get("data", b"{}")
            if isinstance(data_raw, bytes):
                data_raw = data_raw.decode()
            data = json.loads(data_raw)
            event = DomainEvent.model_validate(data)
            msg_id_str = msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id)
            self._pending[event.event_id] = (stream_name, group, msg_id_str)
            yield event

    def _autoclaim(
        self, streams: dict[str, str], group: str, consumer: str, min_idle_ms: int
    ) -> Iterator[DomainEvent]:
        for stream_key in streams:
            cursor = "0-0"
            while True:
                try:
                    resp = self._r.xautoclaim(
                        stream_key,
                        group,
                        consumer,
                        min_idle_time=min_idle_ms,
                        start_id=cursor,
                        count=10,
                    )
                except redis.exceptions.ResponseError:
                    break  # group/stream vanished — next subscribe recreates it
                # Redis 7 returns (next_id, messages, deleted_ids); Redis 6
                # omits the third element.
                next_cursor, messages = resp[0], resp[1]
                yield from self._decode_messages(stream_key, messages, group)
                if not messages or next_cursor in (b"0-0", "0-0"):
                    break  # full PEL scan complete
                # Advance past the last claimed id ourselves: claiming resets
                # idle to 0, so with a small min_idle the server's cursor
                # (inclusive on some implementations) would rescan the same
                # entry forever.
                last_id = messages[-1][0]
                if isinstance(last_id, bytes):
                    last_id = last_id.decode()
                ms, _, seq = last_id.partition("-")
                cursor = f"{ms}-{int(seq) + 1}"

    def _write_journal(self, event: DomainEvent) -> None:
        try:
            path = (
                self._journal_dir
                / f"evt-{event.timestamp.strftime('%Y%m%dT%H%M%S')}-{event.event_id[:8]}.json"
            )
            path.write_text(json.dumps(event.model_dump(mode="json"), indent=2, default=str))
        except Exception:
            pass  # journal is optional


# ---------------------------------------------------------------------------
# In-memory event adapter (for testing / dry-run)
# ---------------------------------------------------------------------------


class InMemoryEventAdapter(EventPort):
    """Simple in-process pub/sub for testing / dry-run. Not thread-safe.

    Generators are finite: they yield the backlog published since this
    group's last read and then return. Per-(group, event_type) cursors let
    a runner loop re-subscribe with the same group and see only new events,
    mirroring (in miniature) how a Redis consumer group resumes.
    """

    def __init__(self) -> None:
        self._published: list[DomainEvent] = []
        self._subscribers: dict[str, list[DomainEvent]] = {}
        self._cursors: dict[tuple[str, str], int] = {}

    def publish(self, event: DomainEvent) -> None:
        self._published.append(event)
        self._subscribers.setdefault(event.type, []).append(event)

    def subscribe(
        self,
        event_type: str,
        group: str = "",
        consumer: str = "",
        stop: Optional[Callable[[], bool]] = None,
    ) -> Iterator[DomainEvent]:
        yield from self.subscribe_many([event_type], group, consumer, stop=stop)

    def subscribe_many(
        self,
        event_types: list[str],
        group: str = "",
        consumer: str = "",
        stop: Optional[Callable[[], bool]] = None,
    ) -> Iterator[DomainEvent]:
        for et in event_types:
            backlog = self._subscribers.get(et, [])
            start = self._cursors.get((group, et), 0)
            for i in range(start, len(backlog)):
                if stop is not None and stop():
                    return
                self._cursors[(group, et)] = i + 1
                yield backlog[i]

    def ack(self, event: DomainEvent, group: str) -> None:  # noqa: ARG002
        return None

    @property
    def all_events(self) -> list[DomainEvent]:
        return list(self._published)

    def events_of_type(self, event_type: str) -> list[DomainEvent]:
        return [e for e in self._published if e.type == event_type]
