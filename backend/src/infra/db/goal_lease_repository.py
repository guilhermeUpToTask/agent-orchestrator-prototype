"""SQLite repository for per-goal worker leases."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, sessionmaker

from src.infra.db._session import run_in_session

_INSERT_SQL = text(
    """
    INSERT OR IGNORE INTO goal_leases (plan_id, goal_id)
    VALUES (:plan_id, :goal_id)
    """
)

_CLAIM_SQL = text(
    """
    UPDATE goal_leases
    SET claimed_by = :worker_id,
        claimed_at = :now_epoch,
        lease_expires_at = :expires_epoch,
        lease_seconds = :lease_seconds
    WHERE plan_id = :plan_id
      AND goal_id = :goal_id
      AND (claimed_by IS NULL OR lease_expires_at < :now_epoch)
    """
)

_HEARTBEAT_SQL = text(
    """
    UPDATE goal_leases
    SET lease_expires_at = :expires_epoch,
        lease_seconds = :lease_seconds
    WHERE plan_id = :plan_id
      AND goal_id = :goal_id
      AND claimed_by = :worker_id
    """
)

_RELEASE_SQL = text(
    """
    UPDATE goal_leases
    SET claimed_by = NULL, claimed_at = NULL,
        lease_expires_at = NULL, lease_seconds = NULL
    WHERE plan_id = :plan_id
      AND goal_id = :goal_id
      AND claimed_by = :worker_id
    """
)

_IS_LIVE_SQL = text(
    """
    SELECT 1 FROM goal_leases
    WHERE plan_id = :plan_id
      AND goal_id = :goal_id
      AND claimed_by IS NOT NULL
      AND lease_expires_at > :now_epoch
    """
)


class SqliteGoalLeaseRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def claim_one_ready_goal(
        self,
        plan_id: str,
        goal_id: str,
        worker_id: str,
        lease_seconds: int,
        now: datetime,
    ) -> bool:
        now_epoch = int(now.timestamp())

        def claim(session: Session) -> bool:
            session.execute(_INSERT_SQL, {"plan_id": plan_id, "goal_id": goal_id})
            result: CursorResult[Any] = session.execute(  # type: ignore[assignment]
                _CLAIM_SQL,
                {
                    "plan_id": plan_id,
                    "goal_id": goal_id,
                    "worker_id": worker_id,
                    "now_epoch": now_epoch,
                    "expires_epoch": now_epoch + lease_seconds,
                    "lease_seconds": lease_seconds,
                },
            )
            return result.rowcount == 1

        return run_in_session(self._session_factory, claim)

    def heartbeat(
        self,
        plan_id: str,
        goal_id: str,
        worker_id: str,
        lease_seconds: int,
        now: datetime,
    ) -> None:
        now_epoch = int(now.timestamp())
        run_in_session(
            self._session_factory,
            lambda session: session.execute(
                _HEARTBEAT_SQL,
                {
                    "plan_id": plan_id,
                    "goal_id": goal_id,
                    "worker_id": worker_id,
                    "expires_epoch": now_epoch + lease_seconds,
                    "lease_seconds": lease_seconds,
                },
            ),
        )

    def release(self, plan_id: str, goal_id: str, worker_id: str) -> None:
        run_in_session(
            self._session_factory,
            lambda session: session.execute(
                _RELEASE_SQL,
                {"plan_id": plan_id, "goal_id": goal_id, "worker_id": worker_id},
            ),
        )

    def is_claim_live(self, plan_id: str, goal_id: str, now: datetime) -> bool:
        now_epoch = int(now.timestamp())

        def check(session: Session) -> bool:
            row = session.execute(
                _IS_LIVE_SQL,
                {"plan_id": plan_id, "goal_id": goal_id, "now_epoch": now_epoch},
            ).one_or_none()
            return row is not None

        return run_in_session(self._session_factory, check)
