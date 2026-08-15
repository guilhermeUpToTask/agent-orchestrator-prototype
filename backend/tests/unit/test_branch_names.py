from __future__ import annotations

from praxis_orchestrator.app.branch_names import (
    cycle_branch,
    goal_branch,
    legacy_plan_branch,
    legacy_task_branch,
    task_branch,
)


def test_cyclic_ladder_names() -> None:
    assert cycle_branch("c1") == "cycle/c1"
    assert goal_branch("g1") == "goal/g1"
    assert task_branch("t1", "r1") == "task/t1/r1"


def test_legacy_names_stay_separate_from_the_cyclic_ladder() -> None:
    # A cyclic plan has NO plan-level branch: the cycle branch is cut straight
    # from the repository default. These two exist only for pre-cyclic rows.
    assert legacy_plan_branch("p1") == "plan/p1"
    assert legacy_task_branch("t1", 2) == "task/t1/a2"


def test_cyclic_task_branch_keys_on_run_not_attempt() -> None:
    # A run id is globally unique, so a retry never collides with a branch a
    # previous attempt left behind. This is the difference the docs got wrong.
    assert task_branch("t1", "run-abc") != legacy_task_branch("t1", 1)
