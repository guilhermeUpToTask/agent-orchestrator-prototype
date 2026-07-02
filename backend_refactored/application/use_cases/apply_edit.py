"""apply_edit — apply a structural edit to a plan's goals/tasks.

Goes through the domain edit_service (which enforces "can't edit a running/terminal
goal" and renumbers positions). Version-CAS on save is the worker-vs-edit race
guard: if the worker advanced the plan between this read and save, the save raises
StaleVersionError and the caller retries or returns 409.

Rebind-on-edit (locked decision):
- RebindTaskAgent (manual agent_id edit) = explicit override — NO auto-rematch.
- EditTaskRequirements = the requirements changed, so match_agent RE-RUNS and the
  task's agent_id is rebound to the new best match.
Capability ids are validated against the catalog at this boundary (DESIGN_NOTES #5)
so a bad id fails loudly (UnknownCapabilityError) instead of silently defaulting.
"""

from __future__ import annotations

from dataclasses import dataclass

from domain.entities.task import Task
from domain.errors.agent_errors import UnknownCapabilityError
from domain.errors.planning_errors import InvalidEditError
from domain.repositories.agent_repo import AgentRepository
from domain.repositories.capability_repo import CapabilityRepository
from domain.services import edit_service
from domain.services.capability_matching import match_agent
from domain.services.lookups import find_goal, find_task
from domain.value_objects.lifecycle import Status

from application.ports import UnitOfWork


@dataclass
class AddTask:
    goal_id: str
    task: Task


@dataclass
class RemoveTask:
    goal_id: str
    task_id: str


@dataclass
class ReorderTasks:
    goal_id: str
    ordered_task_ids: list[str]


@dataclass
class EditTaskRequirements:
    goal_id: str
    task_id: str
    required_capabilities: list[str]


@dataclass
class RebindTaskAgent:
    """Manually reassign a task's agent — an explicit override (no rematch)."""

    goal_id: str
    task_id: str
    agent_id: str


Edit = AddTask | RemoveTask | ReorderTasks | EditTaskRequirements | RebindTaskAgent


def _validate_capability_ids(
    ids: list[str], capabilities: CapabilityRepository
) -> None:
    known = {c.id for c in capabilities.list()}
    for cap_id in ids:
        if cap_id not in known:
            raise UnknownCapabilityError(cap_id, sorted(known))


def apply_edit(
    plan_id: str,
    edit: Edit,
    uow: UnitOfWork,
    capabilities: CapabilityRepository,
    agents: AgentRepository,
) -> None:
    with uow:
        plan = uow.plans.get(plan_id)

        if isinstance(edit, AddTask):
            _validate_capability_ids(edit.task.required_capabilities, capabilities)
            edit_service.add_task(plan.goals, edit.goal_id, edit.task)
        elif isinstance(edit, RemoveTask):
            edit_service.remove_task(plan.goals, edit.goal_id, edit.task_id)
        elif isinstance(edit, ReorderTasks):
            edit_service.reorder_tasks(plan.goals, edit.goal_id, edit.ordered_task_ids)
        elif isinstance(edit, EditTaskRequirements):
            _validate_capability_ids(edit.required_capabilities, capabilities)
            edit_service.edit_task_requirements(
                plan.goals, edit.goal_id, edit.task_id, edit.required_capabilities
            )
            # requirements changed -> re-run the match (locked rebind-on-edit rule)
            task = find_task(find_goal(plan.goals, edit.goal_id), edit.task_id)
            task.agent_id, _ = match_agent(
                task.required_capabilities, agents.list(), agents.default_agent_id()
            )
        elif isinstance(edit, RebindTaskAgent):
            # task-level guard (not the goal-level _assert_editable): rebinding a
            # PENDING task of a RUNNING goal is allowed; a RUNNING/terminal task is not.
            task = find_task(find_goal(plan.goals, edit.goal_id), edit.task_id)
            if task.status != Status.PENDING:
                raise InvalidEditError(
                    f"task '{edit.task_id}' is {task.status.value}; "
                    "only a pending task's agent can be rebound"
                )
            agents.get(edit.agent_id)  # existence check: AgentNotFoundError
            task.agent_id = edit.agent_id
        else:  # exhaustiveness guard
            raise TypeError(f"unknown edit type: {type(edit).__name__}")

        plan.bump_version()
        uow.plans.save(plan)  # version-CAS: StaleVersionError if worker raced
