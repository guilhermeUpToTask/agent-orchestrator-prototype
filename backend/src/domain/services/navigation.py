from __future__ import annotations

from datetime import datetime
from typing import Literal, Union

from src.domain.entities.goal import Goal
from src.domain.entities.task import Task
from src.domain.value_objects.lifecycle import Status, TERMINAL

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
    is a readiness condition with head-of-line semantics: a goal whose head task
    has retry_not_before in the future is blocked as a whole (tasks are a
    sequential chain — the scan never runs a later task past a waiting earlier
    one); the scan then moves on, exactly like a goal whose dependencies are
    unmet.

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

        # Tasks in a goal are a sequential chain: only the HEAD (first non-terminal
        # task in position order) is ever a candidate. A backing-off head blocks
        # the whole goal — never skip ahead to a later task. Cross-goal order is
        # unaffected: the scan moves on to the next goal whose depends_on are met.
        head = next(
            (
                t
                for t in sorted(goal.tasks, key=lambda t: t.position)
                if t.status not in TERMINAL
            ),
            None,
        )
        if head is not None:
            if head.is_ready_at(now):
                return goal, head
            saw_backing_off = True
            continue

        # No actionable tasks left but at least one FAILED -> emit the GOAL_FAILED
        # signal; the worker turns that into the auto-pause (needs-attention). A
        # task with retries left is still actionable (handled above), so a
        # transient failure never reaches here.
        if any(t.status == Status.FAILED for t in goal.tasks):
            return goal, "GOAL_FAILED"
        return goal, None

    return NOT_READY if saw_backing_off else None
