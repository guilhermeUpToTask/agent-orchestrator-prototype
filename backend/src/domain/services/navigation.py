from __future__ import annotations

from datetime import datetime
from typing import Literal, Union

from src.domain.entities.goal import Goal
from src.domain.entities.task import Task
from src.domain.services.dependency_graph import blocked_nodes, ready_nodes
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

    Behavior is unchanged by the additive goal-parallelism primitives below
    (`ready_goal_ids`/`action_for_goal`, domain unfreeze #13) — this function's
    signature and every existing caller/test stay byte-identical; it now just
    shares its per-goal tail with `action_for_goal` instead of owning it alone.
    """
    ordered = sorted(goals, key=lambda g: g.position)
    head_goal = next((goal for goal in ordered if goal.status not in TERMINAL), None)
    if head_goal is None:
        return None

    done_goal_ids = {goal.id for goal in goals if goal.status == Status.DONE}
    if not _goal_ready(head_goal, done_goal_ids):
        return head_goal, "DEPENDENCY_BLOCKED"

    return action_for_goal(head_goal, now)


def action_for_goal(goal: Goal, now: datetime) -> NextAction:
    """The per-goal tail of `next_action`, for a goal the CALLER already
    selected (e.g. one member of `ready_goal_ids`'s result) — never re-derives
    goal selection or dependency readiness itself. Shared so `next_action`
    (position-earliest goal, as always) and goal-level dispatch (caller picks
    any ready goal) can't drift into two different implementations of
    "what does this goal want to do next"."""
    head_task = next(
        (
            task
            for task in sorted(goal.tasks, key=lambda task: task.position)
            if task.status not in TERMINAL
        ),
        None,
    )
    if head_task is not None:
        return (goal, head_task) if head_task.is_ready_at(now) else NOT_READY

    if any(task.status == Status.FAILED for task in goal.tasks):
        return goal, "GOAL_FAILED"
    return goal, None


def ready_goal_ids(goals: list[Goal], now: datetime) -> set[str]:
    """Every non-terminal goal whose `depends_on` is entirely DONE — the
    goal-parallelism readiness primitive (ADR-001, domain unfreeze #13).
    `now` is accepted for signature symmetry with `action_for_goal`/
    `next_action` (a future backoff-aware readiness refinement might need it)
    but is unused today: goal-level dependency readiness has no time
    component, only task-level `retry_not_before` does, and that's checked by
    `action_for_goal` once a specific goal is selected, not here."""
    del now  # see docstring — accepted for symmetry, not used yet
    done_goal_ids = {goal.id for goal in goals if goal.status == Status.DONE}
    edges = {goal.id: list(goal.depends_on) for goal in goals}
    candidate_ids = {goal.id for goal in goals if goal.status not in TERMINAL}
    return ready_nodes(candidate_ids, edges, done_goal_ids)


def plan_can_progress(goals: list[Goal], goal_blocked_ids: set[str], now: datetime) -> bool:
    """True iff at least one non-terminal goal can still make progress on its
    own — i.e. is not itself blocked and does not transitively depend on a
    blocked goal (domain unfreeze #14 — per-goal blocks replace "any active
    block freezes the whole plan"). `goal_blocked_ids` is the set of goal ids
    with an active `PlanBlock` (`Plan.goal_blocks`), supplied by the caller.

    Callers MUST treat "zero non-terminal goals" as a separate case (the
    cycle finished, not a blockage) — this function does not special-case it:
    with no non-terminal goals to check, it correctly returns False, but that
    is NOT the same thing as "stuck." `Plan._recompute_cyclic_status` short-
    circuits before calling this when `execution_goals` has no non-terminal
    entries; do the same in any other caller."""
    del now  # symmetry with ready_goal_ids/action_for_goal; no time component today
    non_terminal_ids = {goal.id for goal in goals if goal.status not in TERMINAL}
    edges = {goal.id: list(goal.depends_on) for goal in goals}
    stuck = blocked_nodes(non_terminal_ids, edges, goal_blocked_ids & non_terminal_ids)
    return bool(non_terminal_ids - stuck)
