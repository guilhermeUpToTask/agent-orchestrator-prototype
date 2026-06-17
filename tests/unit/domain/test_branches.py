"""
tests/unit/domain/test_branches.py — branch naming convention.

Guards the Git ref-collision fix: a task branch must never nest under the goal
branch name (Git can't have goal/<g> be both a branch file and a directory).
"""
from __future__ import annotations

from src.domain.value_objects.branches import goal_branch_name, task_branch_name


def test_goal_branch_name():
    assert goal_branch_name("setup-project") == "goal/setup-project"


def test_task_branch_is_sibling_namespace():
    b = task_branch_name("goal/setup-project", "write-setup-tests")
    assert b == "task/setup-project/write-setup-tests"


def test_task_branch_accepts_bare_goal_name():
    assert task_branch_name("setup-project", "t1") == "task/setup-project/t1"


def test_task_branch_never_nests_under_goal_branch():
    goal = goal_branch_name("g")
    task = task_branch_name(goal, "t")
    # The task branch path must not start with "<goal>/" — that is the exact
    # condition that makes Git refuse to create the ref.
    assert not task.startswith(goal + "/")
