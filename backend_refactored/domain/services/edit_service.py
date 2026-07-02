"""Domain service: structural edits to a plan's goals/tasks.

Lives in services (not on the aggregate) because edit *validation rules* are
domain logic that spans the plan structure and the edit request. The aggregate
delegates here. Editing a goal that is already running/terminal is forbidden —
which is also why next_action can't desync: you can only edit work not yet started.
"""

from __future__ import annotations

from domain.entities.goal import Goal
from domain.entities.task import Task
from domain.services.lookups import find_goal, find_task
from domain.errors.planning_errors import InvalidEditError
from domain.errors.tasks_errors import GoalAlreadyRunningError
from domain.value_objects.lifecycle import Status, TERMINAL


def _assert_editable(goal: Goal) -> None:
    if goal.status == Status.RUNNING or goal.status in TERMINAL:
        raise GoalAlreadyRunningError(goal.id, goal.status.value)


def _renumber(items: list[Task]) -> None:
    """Reassign contiguous 0..n-1 positions after a structural change, preserving
    order (an add/remove can leave gaps like [0,2]; this densifies back to [0,1])."""
    for idx, item in enumerate(sorted(items, key=lambda t: t.position)):
        item.position = idx


def add_task(goals: list[Goal], goal_id: str, task: Task) -> None:
    goal = find_goal(goals, goal_id)
    _assert_editable(goal)
    if any(t.id == task.id for t in goal.tasks):
        raise InvalidEditError(f"task '{task.id}' already exists in goal '{goal_id}'")
    goal.tasks.append(task)
    _renumber(goal.tasks)


# Filter-by-id (not list.remove/pop, which need the object or an index) so a miss
# raises a typed InvalidEditError instead of a bare ValueError.
def remove_task(goals: list[Goal], goal_id: str, task_id: str) -> None:
    goal = find_goal(goals, goal_id)
    _assert_editable(goal)
    before = len(goal.tasks)
    goal.tasks = [t for t in goal.tasks if t.id != task_id]
    if len(goal.tasks) == before:
        raise InvalidEditError(f"task '{task_id}' not found in goal '{goal_id}'")
    _renumber(goal.tasks)


# `ordered_ids` is the goal's task ids in the desired order; each task's `position` is
# written to its index there. The id *set* is only validated (must equal the goal's task
# ids) — position stays the sort key, not id. Operates within a single goal.
def reorder_tasks(goals: list[Goal], goal_id: str, ordered_ids: list[str]) -> None:
    goal = find_goal(goals, goal_id)
    _assert_editable(goal)
    existing = {t.id for t in goal.tasks}
    if set(ordered_ids) != existing:
        raise InvalidEditError(
            f"reorder for goal '{goal_id}' must list exactly its task ids"
        )
    pos = {tid: i for i, tid in enumerate(ordered_ids)}
    for t in goal.tasks:
        t.position = pos[t.id]


def edit_task_requirements(
    goals: list[Goal], goal_id: str, task_id: str, required_capabilities: list[str]
) -> None:
    """Edit a task's capability requirements (does NOT auto-rematch the agent —
    snapshot binding stays; execution re-validates)."""
    goal = find_goal(goals, goal_id)
    _assert_editable(goal)
    task = find_task(goal, task_id)  # one lookup, one error: TaskNotFoundError
    task.required_capabilities = list(required_capabilities)
