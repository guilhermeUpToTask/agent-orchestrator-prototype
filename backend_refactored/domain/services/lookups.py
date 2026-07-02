"""Shared pure lookups over a plan's goal/task tree.

Extracted so the aggregate and the edit service don't each reimplement the same
find-or-raise logic (DRY). These are pure domain functions — no I/O, no state —
and they raise the same domain errors regardless of caller, so lookup semantics
are consistent everywhere.
"""

from __future__ import annotations

from domain.entities.goal import Goal
from domain.entities.task import Task
from domain.errors.tasks_errors import GoalNotFoundError, TaskNotFoundError


def find_goal(goals: list[Goal], goal_id: str) -> Goal:
    for goal in goals:
        if goal.id == goal_id:
            return goal
    raise GoalNotFoundError(goal_id)


def find_task(goal: Goal, task_id: str) -> Task:
    for task in goal.tasks:
        if task.id == task_id:
            return task
    raise TaskNotFoundError(task_id, goal.id)
