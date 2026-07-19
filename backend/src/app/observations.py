"""Runtime-neutral operational observations.

Observations are evidence about execution, not domain facts and not aggregate
state.  They retain provenance and quality so unavailable or estimated values
can never be mistaken for provider-reported measurements.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Protocol, runtime_checkable
from uuid import uuid4


class ObservationSource(str, Enum):
    ORCHESTRATOR = "orchestrator"
    RUNTIME = "runtime"
    PROVIDER = "provider"
    PROCESS = "process"
    LOG_PARSER = "log_parser"
    ESTIMATOR = "estimator"
    LEGACY = "legacy"


class ObservationQuality(str, Enum):
    EXACT = "exact"
    REPORTED = "reported"
    DERIVED = "derived"
    ESTIMATED = "estimated"
    UNAVAILABLE = "unavailable"
    LEGACY_UNKNOWN = "legacy_unknown"


class ObservationKind(str, Enum):
    MODEL_USAGE = "model.usage"
    PROCESS_STARTED = "process.started"
    PROCESS_EXITED = "process.exited"
    PROCESS_TIMED_OUT = "process.timed_out"


@dataclass(frozen=True)
class ObservationCorrelation:
    plan_id: str
    goal_id: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    attempt_id: str | None = None
    attempt_number: int | None = None

    def __post_init__(self) -> None:
        if not self.plan_id:
            raise ValueError("plan_id is required")
        if self.task_id is not None and self.goal_id is None:
            raise ValueError("task correlation requires goal_id")
        if self.run_id is not None and self.task_id is None:
            raise ValueError("run correlation requires task_id")
        if self.attempt_id is not None and self.run_id is None:
            raise ValueError("attempt correlation requires run_id")
        if self.attempt_number is not None:
            if self.task_id is None:
                raise ValueError("attempt_number requires task_id")
            if self.attempt_number < 1:
                raise ValueError("attempt_number must be positive")


@dataclass(frozen=True)
class ModelUsagePayload:
    model_request_count: int
    turn_count: int
    input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    cached_tokens: int | None
    total_tokens: int | None
    model: str | None = None
    provider: str | None = None
    context: str | None = None
    phase: str | None = None
    unavailable_reason: str | None = None
    estimator_name: str | None = None
    estimator_version: str | None = None

    def __post_init__(self) -> None:
        if self.model_request_count < 1:
            raise ValueError("model_request_count must be positive")
        if self.turn_count < 1:
            raise ValueError("turn_count must be positive")
        token_values = (
            self.input_tokens,
            self.output_tokens,
            self.reasoning_tokens,
            self.cached_tokens,
            self.total_tokens,
        )
        if any(value is not None and value < 0 for value in token_values):
            raise ValueError("token counts cannot be negative")


@dataclass(frozen=True)
class ProcessObservationPayload:
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    exit_code: int | None = None
    duration_seconds: float | None = None
    log_path: str | None = None

    def __post_init__(self) -> None:
        if self.stdout_bytes < 0 or self.stderr_bytes < 0:
            raise ValueError("process byte counts cannot be negative")
        if self.duration_seconds is not None and self.duration_seconds < 0:
            raise ValueError("process duration cannot be negative")


ObservationPayload = ModelUsagePayload | ProcessObservationPayload


@dataclass(frozen=True)
class TelemetryObservation:
    correlation: ObservationCorrelation
    observed_at: datetime
    source: ObservationSource
    quality: ObservationQuality
    kind: ObservationKind
    payload: ObservationPayload
    observation_id: str = field(default_factory=lambda: str(uuid4()))
    schema_version: int = 1
    source_sequence: int | None = None

    def __post_init__(self) -> None:
        if not self.observation_id:
            raise ValueError("observation_id is required")
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        if self.schema_version != 1:
            raise ValueError("unsupported observation schema version")
        if self.source_sequence is not None and self.source_sequence < 0:
            raise ValueError("source_sequence cannot be negative")
        if self.kind is ObservationKind.MODEL_USAGE:
            if not isinstance(self.payload, ModelUsagePayload):
                raise ValueError("model usage requires a ModelUsagePayload")
        elif not isinstance(self.payload, ProcessObservationPayload):
            raise ValueError("process observations require a ProcessObservationPayload")
        else:
            if self.kind is ObservationKind.PROCESS_STARTED:
                if self.payload.exit_code is not None or self.payload.duration_seconds is not None:
                    raise ValueError("process.started cannot contain exit data")
            elif self.kind in (ObservationKind.PROCESS_EXITED, ObservationKind.PROCESS_TIMED_OUT):
                if self.payload.duration_seconds is None or self.payload.log_path is None:
                    raise ValueError("process completion requires duration and log path")
            return
        tokens = (
            self.payload.input_tokens,
            self.payload.output_tokens,
            self.payload.reasoning_tokens,
            self.payload.cached_tokens,
            self.payload.total_tokens,
        )
        if self.quality is ObservationQuality.UNAVAILABLE:
            if any(value is not None for value in tokens):
                raise ValueError("unavailable usage cannot contain token counts")
            if not self.payload.unavailable_reason:
                raise ValueError("unavailable usage requires unavailable_reason")
        else:
            if all(value is None for value in tokens):
                raise ValueError("available usage requires at least one token count")
            if self.payload.unavailable_reason is not None:
                raise ValueError("available usage cannot have unavailable_reason")

        if self.quality is ObservationQuality.ESTIMATED:
            if self.source is not ObservationSource.ESTIMATOR:
                raise ValueError("estimated usage requires estimator source")
            if not self.payload.estimator_name or not self.payload.estimator_version:
                raise ValueError("estimated usage requires estimator name and version")
        if (
            self.quality is ObservationQuality.LEGACY_UNKNOWN
            and self.source is not ObservationSource.LEGACY
        ):
            raise ValueError("legacy-unknown quality requires legacy source")


@dataclass(frozen=True)
class PersistedObservation:
    observation: TelemetryObservation
    recorded_at: datetime


class ObservationConflictError(ValueError):
    """One observation ID was reused for different evidence."""


@runtime_checkable
class ObservationRepository(Protocol):
    """Independent append-only operational evidence repository."""

    async def append(self, observation: TelemetryObservation) -> bool:
        """Return True when inserted, False for an identical duplicate."""
        ...

    def get(self, observation_id: str) -> PersistedObservation: ...
