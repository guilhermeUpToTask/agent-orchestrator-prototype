"""
src/domain/value_objects/branches.py — Git branch naming convention.

Single source of truth for the orchestrator's branch names. A task branch is a
*child* of its goal branch in the git history (it forks from the goal branch
tip and merges back), but it must NOT be a child in the branch *name*: Git
stores refs as files, so ``refs/heads/goal/<g>`` (a file) forbids
``refs/heads/goal/<g>/...`` (which needs a directory). Task branches therefore
live in a sibling ``task/`` namespace, keyed by the goal name for grouping.

  goal branch:  goal/<goal-name>
  task branch:  task/<goal-name>/<task-id>
"""
from __future__ import annotations

_GOAL_PREFIX = "goal/"


def goal_branch_name(goal_name: str) -> str:
    """Return the integration branch for a goal: ``goal/<goal-name>``."""
    return f"{_GOAL_PREFIX}{goal_name}"


def task_branch_name(goal_branch: str, task_id: str) -> str:
    """Return the task branch for *task_id* under *goal_branch*.

    Accepts either a goal *name* or a full ``goal/<name>`` branch; the result is
    always ``task/<goal-name>/<task-id>`` so it never nests under the goal ref.
    """
    goal_name = goal_branch.removeprefix(_GOAL_PREFIX) if goal_branch else ""
    return f"task/{goal_name}/{task_id}"
