"""Domain service: structural edits to a plan's goals/tasks.

Lives in services (not on the aggregate) because edit *validation rules* are
domain logic that spans the plan structure and the edit request. The aggregate
delegates here.

Editability (un-freeze #3): a terminal goal is never editable; a RUNNING goal is
editable only while the plan is PAUSED — the pause gate guarantees nothing new
gets claimed, and an attempt already in flight finalizes tolerantly under the
version CAS, so next_action can't desync. Task-level rule: a RUNNING task is
never editable (its finalize looks it up by id), DONE/SKIPPED are history,
FAILED is editable only while paused (the edit-and-retry window), PENDING
always.
"""

from __future__ import annotations

from typing import Protocol, Sequence

from praxis_orchestrator.domain.entities.execution_contracts import ContractCriterion, VerificationStrategy
from praxis_orchestrator.domain.entities.goal import Goal
from praxis_orchestrator.domain.entities.task import Task
from praxis_orchestrator.domain.services.lookups import find_goal, find_task
from praxis_orchestrator.domain.errors.planning_errors import InvalidEditError
from praxis_orchestrator.domain.errors.tasks_errors import GoalAlreadyRunningError
from praxis_orchestrator.domain.value_objects.lifecycle import Status, TERMINAL


def resync_goal_contract(goal: Goal) -> None:
    """Rebuild `goal.contract.tasks` from the live task contracts.

    `planning_handler._enrich_one` constructs each Task with `contract=item`
    where `item` is an element of `goal.contract.tasks`, so the two start as the
    SAME object. Every contract write goes through `model_validate`/`model_copy`,
    which rebinds only the task's reference — so without this the goal contract
    keeps describing a version that no longer exists.

    Re-running `GoalContract`'s validator is the point, not a side effect:
    `goal_criterion_ids` is editable, so an edit can orphan a goal criterion, and
    this is where that surfaces instead of landing silently.
    """
    if goal.contract is None:
        return
    live = {task.id: task.contract for task in goal.tasks if task.contract is not None}
    if not live:
        return
    ordered = [
        live[contract.id] if contract.id in live else contract
        for contract in goal.contract.tasks
    ]
    goal.contract = goal.contract.model_validate(
        {**goal.contract.model_dump(), "tasks": [c.model_dump() for c in ordered]}
    )


def _assert_editable(goal: Goal, *, paused: bool = False) -> None:
    if goal.status in TERMINAL:
        raise GoalAlreadyRunningError(goal.id, goal.status.value)
    if goal.status == Status.RUNNING and not paused:
        raise GoalAlreadyRunningError(goal.id, goal.status.value)


def _assert_task_mutable(task: Task, *, paused: bool = False) -> None:
    if task.status == Status.PENDING:
        return
    if task.status == Status.FAILED and paused:
        return
    hint = "" if paused else " (pause the plan to edit failed tasks)"
    raise InvalidEditError(f"task '{task.id}' is {task.status.value}; not editable{hint}")


class _Positioned(Protocol):
    position: int


def _renumber(items: Sequence[_Positioned]) -> None:
    """Reassign contiguous 0..n-1 positions after a structural change, preserving
    order (an add/remove can leave gaps like [0,2]; this densifies back to [0,1])."""
    for idx, item in enumerate(sorted(items, key=lambda t: t.position)):
        item.position = idx


def add_task(goals: list[Goal], goal_id: str, task: Task, *, paused: bool = False) -> None:
    goal = find_goal(goals, goal_id)
    _assert_editable(goal, paused=paused)
    if any(t.id == task.id for t in goal.tasks):
        raise InvalidEditError(f"task '{task.id}' already exists in goal '{goal_id}'")
    goal.tasks.append(task)
    _renumber(goal.tasks)


# Filter-by-id (not list.remove/pop, which need the object or an index) so a miss
# raises a typed InvalidEditError instead of a bare ValueError.
def remove_task(goals: list[Goal], goal_id: str, task_id: str, *, paused: bool = False) -> None:
    goal = find_goal(goals, goal_id)
    _assert_editable(goal, paused=paused)
    target = next((t for t in goal.tasks if t.id == task_id), None)
    if target is None:
        raise InvalidEditError(f"task '{task_id}' not found in goal '{goal_id}'")
    _assert_task_mutable(target, paused=paused)
    goal.tasks = [t for t in goal.tasks if t.id != task_id]
    _renumber(goal.tasks)


# `ordered_ids` is the goal's task ids in the desired order; each task's `position` is
# written to its index there. The id *set* is only validated (must equal the goal's task
# ids) — position stays the sort key, not id. Operates within a single goal.
def reorder_tasks(
    goals: list[Goal], goal_id: str, ordered_ids: list[str], *, paused: bool = False
) -> None:
    goal = find_goal(goals, goal_id)
    _assert_editable(goal, paused=paused)
    existing = {t.id for t in goal.tasks}
    if set(ordered_ids) != existing:
        raise InvalidEditError(f"reorder for goal '{goal_id}' must list exactly its task ids")
    pos = {tid: i for i, tid in enumerate(ordered_ids)}
    for t in goal.tasks:
        t.position = pos[t.id]


def edit_task_requirements(
    goals: list[Goal],
    goal_id: str,
    task_id: str,
    required_capabilities: list[str],
    *,
    paused: bool = False,
) -> None:
    """Edit a task's capability requirements (does NOT auto-rematch the agent —
    snapshot binding stays; execution re-validates)."""
    goal = find_goal(goals, goal_id)
    _assert_editable(goal, paused=paused)
    task = find_task(goal, task_id)  # one lookup, one error: TaskNotFoundError
    _assert_task_mutable(task, paused=paused)
    task.required_capabilities = list(required_capabilities)
    if task.contract is not None:
        task.amend_contract(required_capabilities=list(required_capabilities))
    resync_goal_contract(goal)


def update_task(
    goals: list[Goal],
    goal_id: str,
    task_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    paused: bool = False,
) -> None:
    """Rename / re-describe a task. Only the provided fields change."""
    goal = find_goal(goals, goal_id)
    _assert_editable(goal, paused=paused)
    task = find_task(goal, task_id)
    _assert_task_mutable(task, paused=paused)
    if name is not None and not name.strip():
        raise InvalidEditError("task name cannot be empty")
    if name is not None or description is not None:
        task.semantic_edit(name=name, description=description)
    resync_goal_contract(goal)


def update_task_contract(
    goals: list[Goal],
    goal_id: str,
    task_id: str,
    *,
    objective: str | None = None,
    acceptance_criteria: list[ContractCriterion] | None = None,
    verification_strategy: VerificationStrategy | None = None,
    allowed_scope: list[str] | None = None,
    forbidden_scope: list[str] | None = None,
    verification_commands: list[str] | None = None,
    goal_criterion_ids: list[str] | None = None,
    required_capabilities: list[str] | None = None,
    paused: bool = False,
) -> None:
    """Repair a frozen contract in place, instead of regenerating the cycle.

    Routes each field by whether it changes what "correct" MEANS
    (`semantic_edit`, which re-authors the tests) or only how correctness is
    CHECKED (`amend_contract`, which keeps them). Both in one call so an operator
    fixing a scope AND a command pays the re-authoring cost at most once.
    """
    goal = find_goal(goals, goal_id)
    _assert_editable(goal, paused=paused)
    task = find_task(goal, task_id)
    _assert_task_mutable(task, paused=paused)
    if task.contract is None:
        raise InvalidEditError(f"task '{task_id}' has no frozen contract to edit")

    semantic = acceptance_criteria is not None or verification_strategy is not None
    if semantic or objective is not None:
        task.semantic_edit(
            description=objective,
            acceptance_criteria=acceptance_criteria,
            verification_strategy=verification_strategy,
        )
    task.amend_contract(
        allowed_scope=allowed_scope,
        forbidden_scope=forbidden_scope,
        verification_commands=verification_commands,
        goal_criterion_ids=goal_criterion_ids,
        required_capabilities=required_capabilities,
    )
    if required_capabilities is not None:
        # keep the task-level list the registry binds against in step with the
        # contract's — they used to diverge silently (EditTaskRequirements wrote
        # only the task, never the contract).
        task.required_capabilities = list(required_capabilities)
    resync_goal_contract(goal)


def _assert_acyclic(goals: list[Goal], goal_id: str, new_deps: list[str]) -> None:
    """Reject a depends_on set that would create a cycle through goal_id."""
    deps = {g.id: list(g.depends_on) for g in goals}
    deps[goal_id] = list(new_deps)
    seen: set[str] = set()
    stack = list(new_deps)
    while stack:
        current = stack.pop()
        if current == goal_id:
            raise InvalidEditError(f"depends_on for goal '{goal_id}' would create a cycle")
        if current in seen:
            continue
        seen.add(current)
        stack.extend(deps.get(current, []))


def update_goal(
    goals: list[Goal],
    goal_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    depends_on: list[str] | None = None,
    paused: bool = False,
) -> None:
    """Rename / re-describe a goal, or rewrite its connections (depends_on).
    Dependency ids must exist, and the new edge set must stay acyclic."""
    goal = find_goal(goals, goal_id)
    _assert_editable(goal, paused=paused)
    if name is not None:
        if not name.strip():
            raise InvalidEditError("goal name cannot be empty")
        goal.name = name
    if description is not None:
        goal.description = description
    if depends_on is not None:
        known = {g.id for g in goals}
        for dep in depends_on:
            if dep not in known:
                raise InvalidEditError(f"unknown goal id '{dep}' in depends_on")
        if goal_id in depends_on:
            raise InvalidEditError(f"goal '{goal_id}' cannot depend on itself")
        positions = {item.id: item.position for item in goals}
        later = sorted(dep for dep in depends_on if positions[dep] >= goal.position)
        if later:
            raise InvalidEditError(f"dependencies for goal '{goal_id}' must precede it: {later}")
        _assert_acyclic(goals, goal_id, depends_on)
        goal.depends_on = list(depends_on)


def remove_goal(goals: list[Goal], goal_id: str, *, paused: bool = False) -> None:
    """Remove a whole goal. Rejected while any of its tasks is RUNNING (even
    paused — the in-flight finalize must find it). Strips the removed id from
    every other goal's depends_on so no dangling edges remain, and renumbers."""
    goal = find_goal(goals, goal_id)
    _assert_editable(goal, paused=paused)
    if any(t.status == Status.RUNNING for t in goal.tasks):
        raise InvalidEditError(f"goal '{goal_id}' has a running task and cannot be removed")
    goals[:] = [g for g in goals if g.id != goal_id]
    for other in goals:
        if goal_id in other.depends_on:
            other.depends_on = [d for d in other.depends_on if d != goal_id]
    _renumber(goals)
