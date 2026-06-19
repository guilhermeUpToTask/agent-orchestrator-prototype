"""
src/app/usecases/task_delete.py — Delete a single task use case.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.domain import TaskStatus
from src.domain.repositories import TaskRepositoryPort


@dataclass(frozen=True)
class TaskDeleteResult:
    task_id: str
    previous_status: TaskStatus


class TaskDeleteUseCase:
    """
    Operator action: permanently remove a single task record.

    Does NOT clean up associated Git branches or Redis leases — use
    ProjectResetUseCase for a full teardown.

    Raises KeyError if the task does not exist.
    """

    def __init__(self, task_repo: TaskRepositoryPort) -> None:
        self._repo = task_repo

    def execute(self, task_id: str) -> TaskDeleteResult:
        task = self._repo.load(task_id)   # raises KeyError if not found
        previous = task.status
        self._repo.delete(task_id)
        return TaskDeleteResult(task_id=task_id, previous_status=previous)
