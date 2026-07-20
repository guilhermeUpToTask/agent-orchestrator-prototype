from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from src.app.observations import (
    ObservationCorrelation,
    ObservationKind,
    ObservationQuality,
    ObservationSource,
    ProcessObservationPayload,
    TelemetryObservation,
)
from src.app.testing.fakes import FakeClock
from src.infra.container import AppContainer
from src.infra.db.observation_repository import SqliteObservationRepository
from src.infra.db.tables import Base
from src.infra.runtime.cli_runner import PiAgentRunner


def _process_observation() -> TelemetryObservation:
    return TelemetryObservation(
        observation_id="process-observation",
        correlation=ObservationCorrelation(plan_id="plan-1"),
        observed_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        source=ObservationSource.PROCESS,
        quality=ObservationQuality.EXACT,
        kind=ObservationKind.PROCESS_EXITED,
        payload=ProcessObservationPayload(
            stdout_bytes=12,
            stderr_bytes=3,
            exit_code=0,
            duration_seconds=0.25,
            log_path="/tmp/attempt.log",
        ),
    )


def test_pi_strips_only_its_provider_prefix_from_model_id() -> None:
    qualified = PiAgentRunner(
        api_key="key",
        model="openrouter:nvidia/nemotron:free",
        backend="openrouter",
        provider_id="openrouter",
    )
    bare = PiAgentRunner(
        api_key="key",
        model="nvidia/nemotron:free",
        backend="openrouter",
        provider_id="openrouter",
    )

    assert qualified._build_cmd("prompt")[2] == "nvidia/nemotron:free"
    assert bare._build_cmd("prompt")[2] == "nvidia/nemotron:free"


def test_container_process_repository_persists_process_observations(tmp_path) -> None:
    container = AppContainer(tmp_path)
    Base.metadata.create_all(container.engine)
    observation = _process_observation()

    assert asyncio.run(container.observation_repository.append(observation)) is True
    assert container.observation_repository.get(observation.observation_id).observation == observation


def test_base_repository_still_rejects_process_observations(tmp_path) -> None:
    container = AppContainer(tmp_path)
    Base.metadata.create_all(container.engine)
    repository = SqliteObservationRepository(
        container.session_factory,
        FakeClock(datetime(2026, 7, 20, tzinfo=timezone.utc)),
    )

    with pytest.raises(ValueError, match="process repository extension"):
        asyncio.run(repository.append(_process_observation()))
