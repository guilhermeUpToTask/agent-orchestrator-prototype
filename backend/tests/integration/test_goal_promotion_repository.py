"""The promotion ledger is transactional, not best-effort.

An evidence record that can silently go missing makes the evidence read model
under-report where code went, so these tests pin the rollback semantics that
the in-memory fake must match exactly.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from src.app.promotion_records import GoalPromotion

pytestmark = pytest.mark.integration


def _promotion(promotion_id: str = "pr1", goal_id: str = "g1") -> GoalPromotion:
    return GoalPromotion(
        id=promotion_id,
        plan_id="p1",
        cycle_id="c1",
        goal_id=goal_id,
        from_ref="goal/g1",
        into_ref="cycle/c1",
        merge_sha="a1b2c3d",
        promoted_at=datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc),
    )


@pytest.fixture(params=["fakes", "sqlite"])
def promotion_env(request, tmp_path):
    """Both backends, because the truth test's value is that they behave the
    same. Mirrors the env_factory pattern in tests/support.py."""
    from tests.support import build_promotion_env

    return build_promotion_env(request.param, tmp_path)


def test_committed_promotion_is_readable(promotion_env) -> None:
    uow = promotion_env.uow
    with uow:
        uow.promotions.add(_promotion())
    with uow:
        found = uow.promotions.list_for_cycle("p1", "c1")
    assert [item.id for item in found] == ["pr1"]
    assert found[0].from_ref == "goal/g1"
    assert found[0].into_ref == "cycle/c1"
    assert found[0].merge_sha == "a1b2c3d"


def test_rollback_discards_the_promotion(promotion_env) -> None:
    uow = promotion_env.uow
    with pytest.raises(RuntimeError):
        with uow:
            uow.promotions.add(_promotion())
            raise RuntimeError("boom")
    with uow:
        assert uow.promotions.list_for_cycle("p1", "c1") == []


def test_promotions_are_scoped_to_their_cycle(promotion_env) -> None:
    uow = promotion_env.uow
    with uow:
        uow.promotions.add(_promotion())
    with uow:
        assert uow.promotions.list_for_cycle("p1", "other-cycle") == []
        assert uow.promotions.list_for_cycle("other-plan", "c1") == []


def test_promotions_with_equal_timestamps_order_by_id(promotion_env) -> None:
    """`_promotion()`'s default `promoted_at` is fixed, which is the normal case
    here (FakeClock does not advance unless a test advances it): two goals
    promoted in one tick get identical timestamps, so the tie-break MUST be the
    id, matching the adapter's `ORDER BY promoted_at, id`. Inserted out of id
    order so an insertion-order-only fake would fail this."""
    uow = promotion_env.uow
    with uow:
        uow.promotions.add(_promotion(promotion_id="pr2", goal_id="g2"))
        uow.promotions.add(_promotion(promotion_id="pr1", goal_id="g1"))
    with uow:
        found = uow.promotions.list_for_cycle("p1", "c1")
    assert [item.id for item in found] == ["pr1", "pr2"]


def test_duplicate_promotion_id_is_rejected(promotion_env) -> None:
    """Promotion recording sits on a retry/re-finalize path (tolerant finalize
    after a replan), so double-recording the same promotion id is a plausible
    bug the ledger must not silently absorb. `goal_promotions.id` is the SQLite
    PRIMARY KEY (IntegrityError); the fake raises RuntimeError for the same
    condition."""
    uow = promotion_env.uow
    with pytest.raises((RuntimeError, IntegrityError)):
        with uow:
            uow.promotions.add(_promotion(promotion_id="pr1"))
            uow.promotions.add(_promotion(promotion_id="pr1"))
