"""apply_edit — apply a structural edit to a plan's goals/tasks.

Goes through the domain edit_service (which enforces "can't edit a running/terminal
goal" and renumbers positions). Version-CAS on save is the worker-vs-edit race
guard: if the worker advanced the plan between this read and save, the save raises
StaleVersionError and the caller retries or returns 409.

Edits do NOT auto-rematch agents (snapshot binding stays); execution re-validates.
"""

from __future__ import annotations

from dataclasses import dataclass

from domain.entities.task import Task
from domain.services import edit_service

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


Edit = AddTask | RemoveTask | ReorderTasks | EditTaskRequirements


def apply_edit(plan_id: str, edit: Edit, uow: UnitOfWork) -> None:
    with uow:
        plan = uow.plans.get(plan_id)

        if isinstance(edit, AddTask):
            edit_service.add_task(plan.goals, edit.goal_id, edit.task)
        elif isinstance(edit, RemoveTask):
            edit_service.remove_task(plan.goals, edit.goal_id, edit.task_id)
        elif isinstance(edit, ReorderTasks):
            edit_service.reorder_tasks(plan.goals, edit.goal_id, edit.ordered_task_ids)
        elif isinstance(edit, EditTaskRequirements):
            edit_service.edit_task_requirements(
                plan.goals, edit.goal_id, edit.task_id, edit.required_capabilities
            )
        else:  # exhaustiveness guard
            raise TypeError(f"unknown edit type: {type(edit).__name__}")

        plan.bump_version()
        uow.plans.save(plan)  # version-CAS: StaleVersionError if worker raced
