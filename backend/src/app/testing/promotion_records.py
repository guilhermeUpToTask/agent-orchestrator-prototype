"""In-memory GoalPromotionRepository with the SQLite adapter's transaction
semantics: staged writes are visible inside the with-block and discarded on
rollback. The truth test only proves something if both backends agree."""

from __future__ import annotations

from src.app.promotion_records import GoalPromotion


class InMemoryGoalPromotionRepository:
    def __init__(self) -> None:
        self._committed: list[GoalPromotion] = []
        self._staged: list[GoalPromotion] = []

    def _begin(self) -> None:
        self._staged = []

    def _commit(self) -> None:
        self._committed.extend(self._staged)
        self._staged = []

    def _rollback(self) -> None:
        self._staged = []

    def add(self, promotion: GoalPromotion) -> None:
        self._staged.append(promotion)

    def list_for_cycle(self, plan_id: str, cycle_id: str) -> list[GoalPromotion]:
        return [
            item
            for item in (*self._committed, *self._staged)
            if item.plan_id == plan_id and item.cycle_id == cycle_id
        ]
