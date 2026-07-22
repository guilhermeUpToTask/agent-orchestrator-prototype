from __future__ import annotations

from datetime import datetime
from typing import Literal, Union

from src.domain.entities.goal import Goal
from src.domain.entities.task import Task
from src.domain.value_objects.lifecycle import Status, TERMINAL

GoalFailed = Literal["GOAL_FAILED"]
DependencyBlocked = Literal["DEPENDENCY_BLOCKED"]
NOT_READY: Literal["NOT_READY"] = "NOT_READY"

NextAction = Union[
    tuple[Goal, Task],
    tuple[Goal, None],
    tuple[Goal, GoalFailed],
    tuple[Goal, DependencyBlocked],
    Literal["NOT_READY"],
    None,
]


def _goal_ready(goal: Goal, done_goal_ids: set[str]) -> bool:
    return all(dep in done_goal_ids for dep in goal.depends_on)


def can_promote_goal(goal: Goal) -> bool:
    """Cyclic goal-promotion eligibility — the single predicate that navigation
    and `ExecutionHandler._reserve_goal_promotion` must agree on (finding #3).

    A goal may merge into the cycle branch only when EVERY task is `DONE` with
    accepted verification evidence. A `SKIPPED` task (legacy iteration-abandonment
    residue, unfreeze #11) or an evidence-less `DONE` means the goal is NOT
    promotable — it must open a block, never close silently.
    """
    return all(task.status == Status.DONE and bool(task.verification_evidence) for task in goal.tasks)


def next_action(goals: list[Goal], now: datetime) -> NextAction:
    """Return work for only the earliest non-terminal goal.

    Position is the scheduling barrier; `depends_on` remains a correctness
    relationship. Backoff, an unresolved failure, or an unmet dependency on the
    head goal blocks every later goal. Navigation is a pure scan and stores no
    cursor.
    """
    ordered = sorted(goals, key=lambda g: g.position)
    head_goal = next((goal for goal in ordered if goal.status not in TERMINAL), None)
    if head_goal is None:
        return None

    done_goal_ids = {goal.id for goal in goals if goal.status == Status.DONE}
    if not _goal_ready(head_goal, done_goal_ids):
        return head_goal, "DEPENDENCY_BLOCKED"

    head_task = next(
        (
            task
            for task in sorted(head_goal.tasks, key=lambda task: task.position)
            if task.status not in TERMINAL
        ),
        None,
    )
    if head_task is not None:
        return (head_goal, head_task) if head_task.is_ready_at(now) else NOT_READY

    if any(task.status == Status.FAILED for task in head_goal.tasks):
        return head_goal, "GOAL_FAILED"
    return head_goal, None
