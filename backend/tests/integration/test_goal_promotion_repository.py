"""The promotion ledger is transactional, not best-effort.

An evidence record that can silently go missing makes the evidence read model
under-report where code went, so these tests pin the rollback semantics that
the in-memory fake must match exactly.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

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
