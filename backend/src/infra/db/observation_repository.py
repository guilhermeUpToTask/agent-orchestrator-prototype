"""SQLite persistence for typed, runtime-neutral operational observations."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, sessionmaker

from src.app.observations import (
    ModelUsagePayload,
    ObservationConflictError,
    ObservationCorrelation,
    ObservationKind,
    ObservationQuality,
    ObservationSource,
    PersistedObservation,
    ProcessObservationPayload,
    TelemetryObservation,
)
from src.app.ports import Clock
from src.infra.db._session import run_in_session

_INSERT_SQL = text(
    """
    INSERT OR IGNORE INTO agent_events
        (event_id, plan_id, goal_id, task_id, run_id, attempt_id, attempt, seq,
         type, observation_kind, source, quality, schema_version,
         source_sequence, payload, occurred_at, recorded_at)
    VALUES
        (:event_id, :plan_id, :goal_id, :task_id, :run_id, :attempt_id,
         :attempt, :seq, :type, :observation_kind, :source, :quality,
         :schema_version, :source_sequence, :payload, :occurred_at, :recorded_at)
    """
)

_SELECT_SQL = text(
    """
    SELECT event_id, plan_id, goal_id, task_id, run_id, attempt_id, attempt,
           occurred_at, recorded_at, source, quality, observation_kind,
           schema_version, source_sequence, payload
    FROM agent_events
    WHERE event_id = :event_id
    """
)


def _legacy_type(kind: ObservationKind) -> str:
    if kind is ObservationKind.MODEL_USAGE:
        return "llm.call"
    return kind.value


def _encode_usage(payload: ModelUsagePayload) -> str:
    """Serialize only the reviewed compatibility/analytics allowlist."""
    values: dict[str, str | None] = {
        "llm_calls": str(payload.model_request_count),
        "turns": str(payload.turn_count),
        "prompt_tokens": (str(payload.input_tokens) if payload.input_tokens is not None else None),
        "completion_tokens": (
            str(payload.output_tokens) if payload.output_tokens is not None else None
        ),
        "reasoning_tokens": (
            str(payload.reasoning_tokens) if payload.reasoning_tokens is not None else None
        ),
        "cached_tokens": (
            str(payload.cached_tokens) if payload.cached_tokens is not None else None
        ),
        "total_tokens": (str(payload.total_tokens) if payload.total_tokens is not None else None),
        "model": payload.model,
        "provider": payload.provider,
        "mode": payload.context,
        "phase": payload.phase,
        "unavailable_reason": payload.unavailable_reason,
        "estimator_name": payload.estimator_name,
        "estimator_version": payload.estimator_version,
    }
    return json.dumps(values, sort_keys=True, separators=(",", ":"))


def _encode_process(payload: ProcessObservationPayload) -> str:
    return json.dumps(
        {
            "duration_seconds": payload.duration_seconds,
            "exit_code": payload.exit_code,
            "log_path": payload.log_path,
            "stderr_bytes": payload.stderr_bytes,
            "stdout_bytes": payload.stdout_bytes,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(str(value))


def _decode(row: object) -> PersistedObservation:
    values = row
    payload_raw: dict[str, Any] = json.loads(str(values[14]))  # type: ignore[index]
    task_id = None if values[3] is None else str(values[3])  # type: ignore[index]
    attempt_number = int(values[6]) if task_id is not None and int(values[6]) > 0 else None  # type: ignore[index]
    kind = ObservationKind(str(values[11]))  # type: ignore[index]
    payload = (
        ModelUsagePayload(
            model_request_count=int(payload_raw["llm_calls"]),
            turn_count=int(payload_raw["turns"]),
            input_tokens=_optional_int(payload_raw.get("prompt_tokens")),
            output_tokens=_optional_int(payload_raw.get("completion_tokens")),
            reasoning_tokens=_optional_int(payload_raw.get("reasoning_tokens")),
            cached_tokens=_optional_int(payload_raw.get("cached_tokens")),
            total_tokens=_optional_int(payload_raw.get("total_tokens")),
            model=payload_raw.get("model"),
            provider=payload_raw.get("provider"),
            context=payload_raw.get("mode"),
            phase=payload_raw.get("phase"),
            unavailable_reason=payload_raw.get("unavailable_reason"),
            estimator_name=payload_raw.get("estimator_name"),
            estimator_version=payload_raw.get("estimator_version"),
        )
        if kind is ObservationKind.MODEL_USAGE
        else ProcessObservationPayload(
            stdout_bytes=int(payload_raw["stdout_bytes"]),
            stderr_bytes=int(payload_raw["stderr_bytes"]),
            exit_code=_optional_int(payload_raw.get("exit_code")),
            duration_seconds=(
                None
                if payload_raw.get("duration_seconds") is None
                else float(payload_raw["duration_seconds"])
            ),
            log_path=payload_raw.get("log_path"),
        )
    )
    observation = TelemetryObservation(
        observation_id=str(values[0]),  # type: ignore[index]
        correlation=ObservationCorrelation(
            plan_id=str(values[1]),  # type: ignore[index]
            goal_id=None if values[2] is None else str(values[2]),  # type: ignore[index]
            task_id=task_id,
            run_id=None if values[4] is None else str(values[4]),  # type: ignore[index]
            attempt_id=None if values[5] is None else str(values[5]),  # type: ignore[index]
            attempt_number=attempt_number,
        ),
        observed_at=datetime.fromisoformat(str(values[7])),  # type: ignore[index]
        source=ObservationSource(str(values[9])),  # type: ignore[index]
        quality=ObservationQuality(str(values[10])),  # type: ignore[index]
        kind=kind,
        schema_version=int(values[12]),  # type: ignore[index]
        source_sequence=(
            None if values[13] is None else int(values[13])  # type: ignore[index]
        ),
        payload=payload,
    )
    recorded_raw = values[8] if values[8] is not None else values[7]  # type: ignore[index]
    return PersistedObservation(
        observation=observation,
        recorded_at=datetime.fromisoformat(str(recorded_raw)),
    )


class SqliteObservationRepository:
    """Independent append-only store over the existing agent_events stream."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        clock: Clock,
    ) -> None:
        self._sf = session_factory
        self._clock = clock

    async def append(self, observation: TelemetryObservation) -> bool:
        if not isinstance(observation.payload, ModelUsagePayload):
            raise ValueError("process observations require the process repository extension")
        return await self._append(observation)

    async def _append(self, observation: TelemetryObservation) -> bool:
        recorded_at = self._clock.now()
        correlation = observation.correlation
        params = {
            "event_id": observation.observation_id,
            "plan_id": correlation.plan_id,
            "goal_id": correlation.goal_id,
            "task_id": correlation.task_id,
            "run_id": correlation.run_id,
            "attempt_id": correlation.attempt_id,
            "attempt": correlation.attempt_number or 0,
            "seq": observation.source_sequence or 0,
            "type": _legacy_type(observation.kind),
            "observation_kind": observation.kind.value,
            "source": observation.source.value,
            "quality": observation.quality.value,
            "schema_version": observation.schema_version,
            "source_sequence": observation.source_sequence,
            "payload": (
                _encode_usage(observation.payload)
                if isinstance(observation.payload, ModelUsagePayload)
                else _encode_process(observation.payload)
            ),
            "occurred_at": observation.observed_at.isoformat(),
            "recorded_at": recorded_at.isoformat(),
        }

        def insert(session: Session) -> bool:
            result: CursorResult[Any] = session.execute(  # type: ignore[assignment]
                _INSERT_SQL, params
            )
            return result.rowcount == 1

        inserted = await asyncio.to_thread(run_in_session, self._sf, insert)
        if inserted:
            return True
        existing = self.get(observation.observation_id)
        if existing.observation != observation:
            raise ObservationConflictError(
                f"observation {observation.observation_id!r} already contains different evidence"
            )
        return False

    def get(self, observation_id: str) -> PersistedObservation:
        with self._sf() as session:
            row = session.execute(
                _SELECT_SQL,
                {"event_id": observation_id},
            ).one_or_none()
        if row is None:
            raise KeyError(observation_id)
        return _decode(row)


class SqliteProcessObservationRepository(SqliteObservationRepository):
    """SQLite observation store extended with process lifecycle telemetry."""

    async def append(self, observation: TelemetryObservation) -> bool:
        return await self._append(observation)
