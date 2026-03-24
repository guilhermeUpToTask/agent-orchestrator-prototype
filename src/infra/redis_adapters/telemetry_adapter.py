from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import redis
import structlog

from src.domain import TelemetryEmitterPort, TelemetryEvent

log = structlog.get_logger(__name__)


class RedisTelemetryEmitter(TelemetryEmitterPort):
    """Persist telemetry events into Redis Streams (append-only)."""

    STREAM_NAME = "telemetry:events"

    def __init__(self, redis_client: redis.Redis, journal_dir: str | Path | None = None) -> None:
        self._r = redis_client
        self._journal_dir: Path | None = None
        if journal_dir is not None:
            self._journal_dir = Path(journal_dir)
            self._journal_dir.mkdir(parents=True, exist_ok=True)

    def emit(self, event: TelemetryEvent) -> None:
        raw = event.model_dump(mode="json")
        payload = json.dumps(raw, default=str)
        self._r.xadd(self.STREAM_NAME, {"data": payload})
        self._write_journal(raw, event.event_id, event.timestamp.isoformat())

    def _write_journal(self, payload: dict, event_id: str, timestamp_iso: str) -> None:
        if self._journal_dir is None:
            return
        try:
            ts = timestamp_iso.replace(":", "").replace("-", "").replace("+00:00", "Z")
            path = self._journal_dir / f"tel-{ts}-{event_id[:8]}.json"
            path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        except Exception:
            pass


class JsonLoggerTelemetryEmitter(TelemetryEmitterPort):
    """Mirror telemetry to structured JSON logs."""

    def emit(self, event: TelemetryEvent) -> None:
        data = event.model_dump(mode="json")
        log.info(
            "telemetry.event",
            timestamp=data["timestamp"],
            event_type=data["event_type"],
            trace_id=data["trace_id"],
            span_id=data["span_id"],
            correlation_id=data.get("correlation_id"),
            causation_id=data.get("causation_id"),
            producer=data["producer"],
            payload=data.get("payload", {}),
        )


class CompositeTelemetryEmitter(TelemetryEmitterPort):
    def __init__(self, emitters: Iterable[TelemetryEmitterPort]) -> None:
        self._emitters = list(emitters)

    def emit(self, event: TelemetryEvent) -> None:
        for emitter in self._emitters:
            emitter.emit(event)


class InMemoryTelemetryEmitter(TelemetryEmitterPort):
    def __init__(self) -> None:
        self.events: list[TelemetryEvent] = []

    def emit(self, event: TelemetryEvent) -> None:
        self.events.append(event)


class FileTelemetryLogEmitter(TelemetryEmitterPort):
    """Append structured telemetry log lines to a project file."""

    def __init__(self, log_path: str | Path) -> None:
        self._log_path = Path(log_path)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: TelemetryEvent) -> None:
        line = json.dumps(event.model_dump(mode="json"), default=str)
        with self._log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
