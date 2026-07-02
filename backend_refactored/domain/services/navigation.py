from __future__ import annotations

from datetime import datetime
from typing import Literal, Union

from domain.entities.goal import Goal
from domain.entities.task import Task
from domain.value_objects.tasks_vos import Status, TERMINAL

GoalFailed = Literal["GOAL_FAILED"]

# "Work remains but nothing is runnable right now" (all actionable tasks gated by
# unexpired backoff). Worker treats this as release-and-recheck, NOT done.
NOT_READY: Literal["NOT_READY"] = "NOT_READY"

NextAction = Union[
    tuple[Goal, Task],
    tuple[Goal, None],
    tuple[Goal, GoalFailed],
    Literal["NOT_READY"],
    None,
]


def _goal_ready(goal: Goal, done_goal_ids: set[str]) -> bool:
    return all(dep in done_goal_ids for dep in goal.depends_on)


def next_action(goals: list[Goal], now: datetime) -> NextAction:
    """Derive the next actionable unit by scanning statuses at time `now`.

    `now` is injected (never read inside) so the scan stays pure/testable. Backoff
    is a readiness condition: a task whose retry_not_before is in the future is
    skipped, exactly like a goal whose dependencies are unmet.

    Returns: (goal,task) run it | (goal,None) close goal | (goal,"GOAL_FAILED")
    apply policy | "NOT_READY" backing off, recheck later | None plan complete.
    """
    done_goal_ids = {g.id for g in goals if g.status == Status.DONE}
    saw_backing_off = False

    for goal in sorted(goals, key=lambda g: g.position):
        if goal.status in TERMINAL:
            continue
        if not _goal_ready(goal, done_goal_ids):
            continue

        goal_has_actionable = False
        for task in sorted(goal.tasks, key=lambda t: t.position):
            if task.status in TERMINAL:
                continue
            goal_has_actionable = True
            if not task.is_ready_at(now):
                saw_backing_off = True
                continue
            return goal, task

        if goal_has_actionable:
            # non-terminal tasks exist but all backing off -> not this goal's turn
            continue

        # No actionable tasks left but at least one FAILED -> emit the GOAL_FAILED signal;
        # the worker turns that into Plan.fail_goal(). A task with retries left is still
        # actionable (handled above), so a transient failure never reaches here.
        if any(t.status == Status.FAILED for t in goal.tasks):
            return goal, "GOAL_FAILED"
        return goal, None

    return NOT_READY if saw_backing_off else None
