"""SQLite adapter for the transactional goal-promotion ledger."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.app.promotion_records import GoalPromotion

_COLUMNS = "id, plan_id, cycle_id, goal_id, from_ref, into_ref, merge_sha, promoted_at"


def _promotion(row: object) -> GoalPromotion:
    values = row  # Row supports positional access; keep ORM types out of the port.
    return GoalPromotion(
        id=str(values[0]),  # type: ignore[index]
        plan_id=str(values[1]),  # type: ignore[index]
        cycle_id=str(values[2]),  # type: ignore[index]
        goal_id=str(values[3]),  # type: ignore[index]
        from_ref=str(values[4]),  # type: ignore[index]
        into_ref=str(values[5]),  # type: ignore[index]
        merge_sha=str(values[6]),  # type: ignore[index]
        promoted_at=datetime.fromisoformat(str(values[7])),  # type: ignore[index]
    )


class SqliteGoalPromotionRepository:
    """Bound to the live UoW session; every write shares the Plan/outbox txn.

    Sharing the transaction is also what keeps this off the write-lock critical
    path: SQLite in WAL mode admits exactly one writer, so an INSERT inside the
    finalize transaction acquires no new lock, where a separate connection would
    become a second contender against the hottest write in the system.
    """

    def __init__(self) -> None:
        self._session: Session | None = None

    def bind(self, session: Session) -> None:
        self._session = session

    def unbind(self) -> None:
        self._session = None

    def _bound(self) -> Session:
        if self._session is None:
            raise RuntimeError(
                "SqliteGoalPromotionRepository used outside a UnitOfWork transaction"
            )
        return self._session

    def add(self, promotion: GoalPromotion) -> None:
        self._bound().execute(
            text(
                f"INSERT INTO goal_promotions ({_COLUMNS}) VALUES "
                "(:id, :plan_id, :cycle_id, :goal_id, :from_ref, :into_ref, "
                ":merge_sha, :promoted_at)"
            ),
            {
                "id": promotion.id,
                "plan_id": promotion.plan_id,
                "cycle_id": promotion.cycle_id,
                "goal_id": promotion.goal_id,
                "from_ref": promotion.from_ref,
                "into_ref": promotion.into_ref,
                "merge_sha": promotion.merge_sha,
                "promoted_at": promotion.promoted_at.isoformat(),
            },
        )

    def list_for_cycle(self, plan_id: str, cycle_id: str) -> list[GoalPromotion]:
        rows = (
            self._bound()
            .execute(
                text(
                    f"SELECT {_COLUMNS} FROM goal_promotions "
                    "WHERE plan_id = :plan_id AND cycle_id = :cycle_id "
                    "ORDER BY promoted_at, id"
                ),
                {"plan_id": plan_id, "cycle_id": cycle_id},
            )
            .all()
        )
        return [_promotion(row) for row in rows]
