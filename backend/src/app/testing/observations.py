"""In-memory observation repository with SQLite-compatible idempotency."""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from src.app.observations import (
    ObservationConflictError,
    PersistedObservation,
    TelemetryObservation,
)


class InMemoryObservationRepository:
    def __init__(self, recorded_at: Callable[[], datetime]) -> None:
        self._recorded_at = recorded_at
        self._observations: dict[str, PersistedObservation] = {}

    async def append(self, observation: TelemetryObservation) -> bool:
        existing = self._observations.get(observation.observation_id)
        if existing is not None:
            if existing.observation != observation:
                raise ObservationConflictError(
                    f"observation {observation.observation_id!r} already contains different evidence"
                )
            return False
        self._observations[observation.observation_id] = PersistedObservation(
            observation=observation,
            recorded_at=self._recorded_at(),
        )
        return True

    def get(self, observation_id: str) -> PersistedObservation:
        try:
            return self._observations[observation_id]
        except KeyError:
            raise KeyError(observation_id) from None

    @property
    def observations(self) -> list[PersistedObservation]:
        return list(self._observations.values())
