"""Typed observation persistence and legacy agent-event compatibility."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from src.app.observations import (
    ModelUsagePayload,
    ObservationConflictError,
    ObservationCorrelation,
    ObservationKind,
    ObservationQuality,
    ObservationSource,
    TelemetryObservation,
)
from src.app.testing.fakes import FakeClock
from src.app.testing.observations import InMemoryObservationRepository
from src.domain.events.agent_events import AgentEvent
from src.infra.db.agent_event_sink import SqliteAgentEventSink
from src.infra.db.engine import build_engine, make_session_factory
from src.infra.db.observation_repository import SqliteObservationRepository
from src.infra.db.tables import Base

pytestmark = pytest.mark.integration

T0 = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


def _usage(
    *,
    observation_id: str = "obs-1",
    quality: ObservationQuality = ObservationQuality.REPORTED,
    total_tokens: int | None = 15,
) -> TelemetryObservation:
    unavailable = quality is ObservationQuality.UNAVAILABLE
    return TelemetryObservation(
        observation_id=observation_id,
        correlation=ObservationCorrelation(
            plan_id="p1",
            goal_id="g1",
            task_id="t1",
            run_id="run-1",
            attempt_id="attempt-1",
            attempt_number=3,
        ),
        observed_at=T0,
        source=ObservationSource.PROVIDER,
        quality=quality,
        kind=ObservationKind.MODEL_USAGE,
        source_sequence=7,
        payload=ModelUsagePayload(
            model_request_count=2,
            turn_count=2,
            input_tokens=None if unavailable else 10,
            output_tokens=None if unavailable else 5,
            reasoning_tokens=None,
            cached_tokens=None,
            total_tokens=None if unavailable else total_tokens,
            model="model-x",
            provider="provider-x",
            context="execution",
            phase="running",
            unavailable_reason=("provider_did_not_report_usage" if unavailable else None),
        ),
    )


@pytest.fixture(params=("memory", "sqlite"))
def repository(request, tmp_path):
    clock = FakeClock(T0)
    if request.param == "memory":
        return InMemoryObservationRepository(clock.now)
    engine = build_engine(f"sqlite:///{tmp_path / 'observations.db'}")
    Base.metadata.create_all(engine)
    return SqliteObservationRepository(make_session_factory(engine), clock)


def test_append_round_trips_correlation_provenance_and_quality(repository):
    observation = _usage()

    assert asyncio.run(repository.append(observation)) is True
    stored = repository.get(observation.observation_id)

    assert stored.observation == observation
    assert stored.recorded_at == T0


def test_identical_duplicate_is_idempotent_but_conflict_is_rejected(repository):
    observation = _usage()
    assert asyncio.run(repository.append(observation)) is True
    assert asyncio.run(repository.append(observation)) is False

    conflict = replace(
        observation,
        payload=replace(observation.payload, total_tokens=16),
    )
    with pytest.raises(ObservationConflictError):
        asyncio.run(repository.append(conflict))


def test_unavailable_usage_round_trips_as_none_not_zero(repository):
    observation = _usage(
        observation_id="obs-unavailable",
        quality=ObservationQuality.UNAVAILABLE,
        total_tokens=None,
    )

    asyncio.run(repository.append(observation))
    payload = repository.get(observation.observation_id).observation.payload

    assert payload.input_tokens is None
    assert payload.output_tokens is None
    assert payload.total_tokens is None
    assert payload.unavailable_reason == "provider_did_not_report_usage"


def test_observation_validation_rejects_false_unavailable_and_unsafe_estimate():
    with pytest.raises(ValueError, match="unavailable usage cannot contain"):
        replace(_usage(), quality=ObservationQuality.UNAVAILABLE)
    with pytest.raises(ValueError, match="estimator source"):
        replace(_usage(), quality=ObservationQuality.ESTIMATED)
    with pytest.raises(ValueError, match="estimator name and version"):
        replace(
            _usage(),
            quality=ObservationQuality.ESTIMATED,
            source=ObservationSource.ESTIMATOR,
        )


def test_typed_row_preserves_legacy_shape_with_allowlisted_payload(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'typed.db'}")
    Base.metadata.create_all(engine)
    repository = SqliteObservationRepository(
        make_session_factory(engine),
        FakeClock(T0),
    )

    observation = _usage()
    assert asyncio.run(repository.append(observation)) is True
    assert asyncio.run(repository.append(observation)) is False

    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT type, observation_kind, source, quality, schema_version, "
                "payload FROM agent_events WHERE event_id = 'obs-1'"
            )
        ).one()
        count = connection.execute(
            text("SELECT COUNT(*) FROM agent_events WHERE event_id = 'obs-1'")
        ).scalar_one()
    assert count == 1
    assert tuple(row[:5]) == (
        "llm.call",
        "model.usage",
        "provider",
        "reported",
        1,
    )
    payload = json.loads(row[5])
    assert set(payload) == {
        "cached_tokens",
        "completion_tokens",
        "estimator_name",
        "estimator_version",
        "llm_calls",
        "mode",
        "model",
        "phase",
        "prompt_tokens",
        "provider",
        "reasoning_tokens",
        "total_tokens",
        "turns",
        "unavailable_reason",
    }
    assert not {"prompt", "completion", "messages", "source_code"} & set(payload)


def test_legacy_agent_event_is_preserved_and_marked_unknown(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    Base.metadata.create_all(engine)
    sf = make_session_factory(engine)
    event = AgentEvent(
        event_id="legacy-1",
        occurred_at=T0,
        plan_id="p1",
        task_id="t1",
        attempt=1,
        seq=4,
        type="step",
        payload={"message": "safe summary"},
    )

    asyncio.run(SqliteAgentEventSink(sf).emit(event))

    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT type, observation_kind, source, quality, schema_version, "
                "source_sequence, occurred_at, recorded_at, run_id, attempt_id "
                "FROM agent_events WHERE event_id = 'legacy-1'"
            )
        ).one()
    assert tuple(row[:6]) == (
        "step",
        "step",
        "legacy",
        "legacy_unknown",
        0,
        4,
    )
    assert row[6] == T0.isoformat()
    assert row[7] is not None
    assert row[8] is None and row[9] is None
