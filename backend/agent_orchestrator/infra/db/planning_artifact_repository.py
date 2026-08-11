"""
agent_orchestrator/infra/db/planning_artifact_repository.py — the PlanningArtifactStore port.

Each write is its own short transaction (`run_in_session`), never part of the
plan UnitOfWork. That is the `ChatStore` rule, for the same reason: an artifact
recorded so the RETRY can learn from it must not roll back with the transaction
it exists to outlive.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from agent_orchestrator.app.ports import PlanningArtifact
from agent_orchestrator.infra.db._session import run_in_session

_INSERT_SQL = text(
    """
    INSERT INTO planning_artifacts (
        id, plan_id, goal_id, purpose, operation_id, sequence,
        input_fingerprint, outcome, payload_json, rejection_reasons_json,
        turns_used, created_at
    ) VALUES (
        :id, :plan_id, :goal_id, :purpose, :operation_id, :sequence,
        :input_fingerprint, :outcome, :payload_json, :rejection_reasons_json,
        :turns_used, :created_at
    )
    """
)

# `goal_id IS :goal_id` rather than `= :goal_id` so a plan-wide purpose
# (NULL goal) matches, instead of silently returning nothing.
_SELECT_SQL = text(
    """
    SELECT id, plan_id, goal_id, purpose, operation_id, sequence,
           input_fingerprint, outcome, payload_json, rejection_reasons_json,
           turns_used, created_at
    FROM planning_artifacts
    WHERE plan_id = :plan_id AND purpose = :purpose AND goal_id IS :goal_id
    ORDER BY sequence DESC, created_at DESC
    LIMIT :limit
    """
)

# Deliberately without the `goal_id` predicate: `_SELECT_SQL` uses
# `goal_id IS :goal_id`, so passing NULL asks for the PLAN-WIDE artifacts, not
# for all of them. A goal-scoped purpose was therefore unreachable without
# knowing every goal id up front.
_SELECT_ACROSS_GOALS_SQL = text(
    """
    SELECT id, plan_id, goal_id, purpose, operation_id, sequence,
           input_fingerprint, outcome, payload_json, rejection_reasons_json,
           turns_used, created_at
    FROM planning_artifacts
    WHERE plan_id = :plan_id AND purpose = :purpose
    ORDER BY sequence DESC, created_at DESC
    LIMIT :limit
    """
)

_NEXT_SEQUENCE_SQL = text(
    """
    SELECT COALESCE(MAX(sequence), 0) + 1
    FROM planning_artifacts
    WHERE plan_id = :plan_id AND purpose = :purpose AND goal_id IS :goal_id
    """
)

_DELETE_SQL = text(
    """
    DELETE FROM planning_artifacts
    WHERE plan_id = :plan_id AND purpose = :purpose AND goal_id IS :goal_id
    """
)


def _row_to_artifact(values: Sequence[Any]) -> PlanningArtifact:
    payload_raw = values[8]
    return PlanningArtifact(
        plan_id=str(values[1]),
        goal_id=None if values[2] is None else str(values[2]),
        purpose=str(values[3]),
        operation_id=None if values[4] is None else str(values[4]),
        sequence=int(values[5]),
        input_fingerprint=str(values[6]),
        outcome=str(values[7]),
        payload=None if payload_raw is None else json.loads(str(payload_raw)),
        rejection_reasons=tuple(json.loads(str(values[9]))),
        turns_used=None if values[10] is None else int(values[10]),
        created_at=datetime.fromisoformat(str(values[11])),
    )


class SqlitePlanningArtifactRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def append(self, artifact: PlanningArtifact) -> None:
        from agent_orchestrator.domain.factories.identity import new_id

        def write(session: Session) -> None:
            key = {
                "plan_id": artifact.plan_id,
                "purpose": artifact.purpose,
                "goal_id": artifact.goal_id,
            }
            # The caller may pass sequence=0 meaning "next"; a real sequence is
            # honoured so a replay of the same attempt stays idempotent.
            sequence = artifact.sequence or int(
                session.execute(_NEXT_SEQUENCE_SQL, key).scalar_one()
            )
            session.execute(
                _INSERT_SQL,
                {
                    **key,
                    "id": new_id(),
                    "operation_id": artifact.operation_id,
                    "sequence": sequence,
                    "input_fingerprint": artifact.input_fingerprint,
                    "outcome": artifact.outcome,
                    "payload_json": (
                        None if artifact.payload is None else json.dumps(artifact.payload)
                    ),
                    "rejection_reasons_json": json.dumps(list(artifact.rejection_reasons)),
                    "turns_used": artifact.turns_used,
                    "created_at": artifact.created_at.isoformat(),
                },
            )

        run_in_session(self._sf, write)

    def latest(
        self, plan_id: str, purpose: str, *, goal_id: str | None = None, limit: int = 5
    ) -> list[PlanningArtifact]:
        with self._sf() as session:
            rows = session.execute(
                _SELECT_SQL,
                {"plan_id": plan_id, "purpose": purpose, "goal_id": goal_id, "limit": limit},
            ).all()
        return [_row_to_artifact(row) for row in rows]

    def latest_across_goals(
        self, plan_id: str, purpose: str, *, limit: int = 5
    ) -> list[PlanningArtifact]:
        with self._sf() as session:
            rows = session.execute(
                _SELECT_ACROSS_GOALS_SQL,
                {"plan_id": plan_id, "purpose": purpose, "limit": limit},
            ).all()
        return [_row_to_artifact(row) for row in rows]

    def clear(self, plan_id: str, purpose: str, *, goal_id: str | None = None) -> None:
        def write(session: Session) -> None:
            session.execute(
                _DELETE_SQL, {"plan_id": plan_id, "purpose": purpose, "goal_id": goal_id}
            )

        run_in_session(self._sf, write)


__all__ = ["SqlitePlanningArtifactRepository"]
