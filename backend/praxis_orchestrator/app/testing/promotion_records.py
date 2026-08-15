"""In-memory GoalPromotionRepository, matched to the SQLite adapter's WRITE-path
transaction semantics: `add()` outside a `with uow:` block raises instead of
silently accepting a write that the next `_begin()` would discard, a duplicate
`id` is rejected instead of silently accepted, and `list_for_cycle` returns rows
in the same `(promoted_at, id)` order the adapter's `ORDER BY` produces — the
truth test only proves something if both backends agree, and `promoted_at` ties
are the common case here (`FakeClock` does not advance on its own), so an
insertion-order fake would pass tests a SQLite-backed run fails.

Reads are the one deliberate asymmetry, matching
`InMemoryExecutionRecordRepository.list_runs`/`list_attempts`
(praxis_orchestrator/app/testing/execution_records.py): `list_for_cycle` serves committed data
outside a transaction rather than raising. `SqliteGoalPromotionRepository`
raises unconditionally when unbound because it has no committed store to fall
back to without a live session; production code always reads through a bound
`with uow:` block regardless, so this fake convenience does not mask a bug the
way an unguarded `add()` would.
"""

from __future__ import annotations

from praxis_orchestrator.app.promotion_records import GoalPromotion


class InMemoryGoalPromotionRepository:
    def __init__(self) -> None:
        self._committed: list[GoalPromotion] = []
        self._staged: list[GoalPromotion] | None = None

    def _begin(self) -> None:
        self._staged = []

    def _commit(self) -> None:
        assert self._staged is not None
        self._committed.extend(self._staged)
        self._staged = None

    def _rollback(self) -> None:
        self._staged = None

    def _bound(self) -> list[GoalPromotion]:
        if self._staged is None:
            raise RuntimeError(
                "InMemoryGoalPromotionRepository used outside a UnitOfWork transaction"
            )
        return self._staged

    def add(self, promotion: GoalPromotion) -> None:
        staged = self._bound()
        if any(item.id == promotion.id for item in (*self._committed, *staged)):
            raise RuntimeError(f"duplicate goal promotion {promotion.id!r}")
        staged.append(promotion)

    def list_for_cycle(self, plan_id: str, cycle_id: str) -> list[GoalPromotion]:
        staged = self._staged or []
        return sorted(
            (
                item
                for item in (*self._committed, *staged)
                if item.plan_id == plan_id and item.cycle_id == cycle_id
            ),
            key=lambda item: (item.promoted_at, item.id),
        )
