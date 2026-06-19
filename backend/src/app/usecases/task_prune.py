"""
src/app/usecases/task_prune.py — Bulk-delete tasks use case.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.domain import TaskStatus
from src.domain.repositories import TaskRepositoryPort


@dataclass(frozen=True)
class TaskPruneResult:
    deleted: list[str]         # task_ids that were deleted
    filter_statuses: set[TaskStatus] | None  # None means "all"

    @property
    def count(self) -> int:
        return len(self.deleted)


class TaskPruneUseCase:
    """
    Operator action: bulk-delete task records, optionally filtered by status.

    filter_statuses=None  → delete ALL tasks
    filter_statuses={...} → delete only tasks whose status is in the set
    """

    def __init__(self, task_repo: TaskRepositoryPort) -> None:
        self._repo = task_repo

    def execute(
        self,
        filter_statuses: set[TaskStatus] | None = None,
    ) -> TaskPruneResult:
        all_tasks = self._repo.list_all()

        targets = (
            all_tasks
            if filter_statuses is None
            else [t for t in all_tasks if t.status in filter_statuses]
        )

        deleted = []
        for task in targets:
            self._repo.delete(task.task_id)
            deleted.append(task.task_id)

        return TaskPruneResult(deleted=deleted, filter_statuses=filter_statuses)
