import json
import pathlib

import fakeredis

from src.domain import TelemetryEvent
from src.infra.redis_adapters.telemetry_adapter import (
    CompositeTelemetryEmitter,
    FileTelemetryLogEmitter,
    InMemoryTelemetryEmitter,
    RedisTelemetryEmitter,
)


def test_redis_telemetry_emitter_publishes_and_journals(tmp_path):
    redis_client = fakeredis.FakeRedis()
    emitter = RedisTelemetryEmitter(redis_client, journal_dir=tmp_path / "telemetry" / "events")

    event = TelemetryEvent(
        event_type="agent.started",
        trace_id="trace-1",
        span_id="span-1",
        producer="worker",
        payload={"task_id": "t-1"},
    )
    emitter.emit(event)

    msg = redis_client.xread({"telemetry:events": "0"})
    assert len(msg) == 1
    payload = json.loads(msg[0][1][0][1][b"data"])
    assert payload["event_type"] == "agent.started"

    journal_files = list((tmp_path / "telemetry" / "events").glob("*.json"))
    assert len(journal_files) == 1


def test_redis_telemetry_emitter_ignores_journal_write_failure(monkeypatch):
    redis_client = fakeredis.FakeRedis()
    emitter = RedisTelemetryEmitter(redis_client, journal_dir="/tmp/nowhere")
    event = TelemetryEvent(
        event_type="agent.failed",
        trace_id="trace-2",
        span_id="span-2",
        producer="worker",
    )

    def fail_write(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(pathlib.Path, "write_text", fail_write)
    emitter.emit(event)

    msg = redis_client.xread({"telemetry:events": "0"})
    assert len(msg) == 1


def test_file_telemetry_log_emitter_appends_jsonl(tmp_path):
    path = tmp_path / "telemetry" / "logs" / "telemetry.jsonl"
    emitter = FileTelemetryLogEmitter(path)

    emitter.emit(
        TelemetryEvent(
            event_type="state.updated",
            trace_id="trace-3",
            span_id="span-3",
            producer="state",
            payload={"key": "current_arch"},
        )
    )
    emitter.emit(
        TelemetryEvent(
            event_type="state.updated",
            trace_id="trace-3",
            span_id="span-4",
            producer="state",
            payload={"key": "context"},
        )
    )

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["payload"]["key"] == "current_arch"


def test_composite_emits_to_all_children():
    a = InMemoryTelemetryEmitter()
    b = InMemoryTelemetryEmitter()
    emitter = CompositeTelemetryEmitter([a, b])

    event = TelemetryEvent(
        event_type="goal.started",
        trace_id="trace-4",
        span_id="span-5",
        producer="orchestrator",
    )
    emitter.emit(event)

    assert len(a.events) == 1
    assert len(b.events) == 1
