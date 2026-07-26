"""Enrichment progress survives the failure that produced it.

Before this, a rejected submission lived only in the reasoner's in-process
message list. When the turn budget then ran out that list was discarded and the
retry rebuilt its prompt from scratch with zero memory — observed live, a
session spent its whole budget on reads, died, and the next attempt started
again from nothing.

Both implementations are exercised so the in-memory fake and real SQLite cannot
drift; the truth test depends on that equivalence.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.app.ports import PlanningArtifact
from src.app.testing.fakes import InMemoryPlanningArtifactStore
from src.infra.db.planning_artifact_repository import SqlitePlanningArtifactRepository

pytestmark = pytest.mark.integration

NOW = datetime(2026, 7, 26, tzinfo=timezone.utc)
PLAN, GOAL = "plan-1", "goal-1"


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path, monkeypatch):
    if request.param == "memory":
        return InMemoryPlanningArtifactStore()
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
    from src.domain.aggregates.planner_orchestrator import Plan
    from src.domain.entities.project_definition import ProjectDefinition
    from src.infra.container import AppContainer
    from src.infra.db.tables import Base

    container = AppContainer(orchestrator_home=tmp_path)
    Base.metadata.create_all(container.engine)
    container.project_repo.add(ProjectDefinition(id="p", name="p", repo_url=None))
    with container.new_unit_of_work() as uow:
        uow.plans.save(Plan(id=PLAN, project_id="p", brief="b"))
    return SqlitePlanningArtifactRepository(container.session_factory)


def _artifact(**overrides) -> PlanningArtifact:
    values = {
        "plan_id": PLAN,
        "goal_id": GOAL,
        "purpose": "goal_contract",
        "sequence": 0,  # 0 means "assign the next one"
        "input_fingerprint": "fp-1",
        "outcome": "rejected",
        "created_at": NOW,
        "payload": {"tasks": [{"allowed_scope": ["implementation"]}]},
        "rejection_reasons": ("allowed_scope: 'implementation' is a capability id, not a path",),
        "turns_used": 5,
    }
    values.update(overrides)
    return PlanningArtifact(**values)  # type: ignore[arg-type]


def test_a_rejected_submission_survives_and_reads_back_whole(store) -> None:
    store.append(_artifact())

    (found,) = store.latest(PLAN, "goal_contract", goal_id=GOAL)

    assert found.outcome == "rejected"
    assert found.payload == {"tasks": [{"allowed_scope": ["implementation"]}]}
    assert found.rejection_reasons == (
        "allowed_scope: 'implementation' is a capability id, not a path",
    )
    assert found.turns_used == 5
    assert found.input_fingerprint == "fp-1"


def test_attempts_accumulate_newest_first_rather_than_overwriting(store) -> None:
    """The sequence is why this is a table and not a column: `_start_operation`
    REUSES one planning_operations row across a whole outage, so a single column
    would be overwritten on every attempt."""
    store.append(_artifact(rejection_reasons=("first",)))
    store.append(_artifact(rejection_reasons=("second",), created_at=NOW + timedelta(minutes=1)))
    store.append(_artifact(rejection_reasons=("third",), created_at=NOW + timedelta(minutes=2)))

    found = store.latest(PLAN, "goal_contract", goal_id=GOAL)

    assert [a.rejection_reasons[0] for a in found] == ["third", "second", "first"]
    assert [a.sequence for a in found] == [3, 2, 1]


def test_a_plan_wide_purpose_uses_a_null_goal_and_still_matches(store) -> None:
    """Cycle architecture has no goal. `goal_id IS :goal_id` rather than `=`,
    or a NULL never matches and the whole feature silently does nothing."""
    store.append(_artifact(goal_id=None, purpose="cycle_architecture"))

    assert len(store.latest(PLAN, "cycle_architecture", goal_id=None)) == 1
    assert store.latest(PLAN, "cycle_architecture", goal_id=GOAL) == []


def test_artifacts_are_scoped_per_goal(store) -> None:
    store.append(_artifact(goal_id="goal-1"))
    store.append(_artifact(goal_id="goal-2"))

    assert len(store.latest(PLAN, "goal_contract", goal_id="goal-1")) == 1
    assert len(store.latest(PLAN, "goal_contract", goal_id="goal-2")) == 1


def test_the_operator_can_drop_a_goals_history(store) -> None:
    """The escape hatch for when the replay heuristics are wrong."""
    store.append(_artifact())
    store.append(_artifact(goal_id="goal-2"))

    store.clear(PLAN, "goal_contract", goal_id=GOAL)

    assert store.latest(PLAN, "goal_contract", goal_id=GOAL) == []
    assert len(store.latest(PLAN, "goal_contract", goal_id="goal-2")) == 1


def test_limit_bounds_the_read(store) -> None:
    for index in range(6):
        store.append(_artifact(created_at=NOW + timedelta(minutes=index)))

    assert len(store.latest(PLAN, "goal_contract", goal_id=GOAL, limit=2)) == 2
