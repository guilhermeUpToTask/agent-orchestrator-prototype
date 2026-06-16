"""
Unit tests for ResumePhaseDispatchUseCase — the recovery path that re-dispatches
active-phase goals which never got created (e.g. a goal whose branch creation
failed during approve-architecture).
"""
from unittest.mock import MagicMock

import pytest

from src.app.planning.sessions.usecases import ResumePhaseDispatchUseCase
from src.domain.aggregates.project_plan import (
    Phase,
    PhaseStatus,
    ProjectPlan,
    ProjectPlanStatus,
)
from src.domain.aggregates.planner_session import PlannerMode, PlannerSessionStatus
from src.domain.errors import InvalidPlanTransitionError
from src.domain.value_objects.goal import GoalSpec


def _phase_active_plan(goal_names: list[str]) -> ProjectPlan:
    """A plan in PHASE_ACTIVE with one active phase listing *goal_names*."""
    return ProjectPlan(
        plan_id="plan-1",
        status=ProjectPlanStatus.PHASE_ACTIVE,
        current_phase_index=0,
        phases=[
            Phase(
                index=0,
                name="Foundation",
                goal="Stand up the backend",
                goal_names=list(goal_names),
                status=PhaseStatus.ACTIVE,
                exit_criteria="app starts",
                lessons="",
            )
        ],
    )


def _goal(goal_id: str, name: str):
    g = MagicMock()
    g.goal_id = goal_id
    g.name = name
    g.feature_tag = None
    return g


def _make_uc(plan, existing_goal_names, goal_init, support, event_port=None):
    plan_repo = MagicMock()
    plan_repo.load.return_value = plan
    session_repo = MagicMock()
    # a completed ARCHITECTURE session so _lookup_spec considers it;
    # support.find_goal_spec is what actually returns the spec.
    arch_session = MagicMock()
    arch_session.status = PlannerSessionStatus.COMPLETED
    arch_session.mode = PlannerMode.ARCHITECTURE
    session_repo.list_all.return_value = [arch_session]
    goal_repo = MagicMock()
    goal_repo.list_all.return_value = [
        _goal(f"goal-{n}", n) for n in existing_goal_names
    ]
    uc = ResumePhaseDispatchUseCase(
        plan_repo=plan_repo,
        session_repo=session_repo,
        goal_repo=goal_repo,
        goal_init=goal_init,
        support=support,
        event_port=event_port,
    )
    return uc, plan_repo


def test_dispatches_only_missing_goals():
    plan = _phase_active_plan(["setup-backend", "define-product-model"])
    # setup-backend already exists; only define-product-model is missing
    support = MagicMock()
    support.find_goal_spec.side_effect = lambda _s, name: GoalSpec(
        name=name, description="d", tasks=[]
    )
    goal_init = MagicMock()
    goal_init.execute.side_effect = lambda spec: _goal("goal-x", spec.name)
    event_port = MagicMock()

    uc, plan_repo = _make_uc(plan, ["setup-backend"], goal_init, support, event_port)
    result = uc.execute()

    # Only the missing goal was dispatched
    assert goal_init.execute.call_count == 1
    dispatched_spec = goal_init.execute.call_args[0][0]
    assert dispatched_spec.name == "define-product-model"
    assert result.goals_dispatched == ["goal-x"]
    assert result.goals_failed == []
    # goal.unblocked emitted for the dispatched goal
    assert event_port.publish.call_count == 1
    plan_repo.save.assert_called()


def test_noop_when_all_present():
    plan = _phase_active_plan(["a", "b"])
    support = MagicMock()
    goal_init = MagicMock()
    uc, _ = _make_uc(plan, ["a", "b"], goal_init, support)
    result = uc.execute()
    assert result.goals_dispatched == []
    assert result.goals_failed == []
    goal_init.execute.assert_not_called()


def test_records_failure_when_spec_missing():
    plan = _phase_active_plan(["ghost"])
    support = MagicMock()
    support.find_goal_spec.return_value = None  # no spec anywhere
    goal_init = MagicMock()
    uc, _ = _make_uc(plan, [], goal_init, support)
    result = uc.execute()
    assert result.goals_dispatched == []
    assert len(result.goals_failed) == 1
    assert result.goals_failed[0].goal_name == "ghost"
    goal_init.execute.assert_not_called()


def test_records_failure_when_dispatch_raises():
    plan = _phase_active_plan(["boom"])
    support = MagicMock()
    support.find_goal_spec.side_effect = lambda _s, name: GoalSpec(
        name=name, description="d", tasks=[]
    )
    goal_init = MagicMock()
    goal_init.execute.side_effect = RuntimeError("git exploded")
    uc, _ = _make_uc(plan, [], goal_init, support)
    result = uc.execute()
    assert result.goals_dispatched == []
    assert len(result.goals_failed) == 1
    assert result.goals_failed[0].goal_name == "boom"
    assert "git exploded" in result.goals_failed[0].error


def test_rejects_wrong_plan_status():
    plan = ProjectPlan(plan_id="p", status=ProjectPlanStatus.ARCHITECTURE)
    support = MagicMock()
    goal_init = MagicMock()
    uc, _ = _make_uc(plan, [], goal_init, support)
    with pytest.raises(InvalidPlanTransitionError):
        uc.execute()
