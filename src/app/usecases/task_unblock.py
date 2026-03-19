"""
src/app/usecases/task_unblock.py — Unblock dependent tasks use case.

When a task completes, scan all tasks that declared it as a dependency
and dispatch any that are now fully unblocked (all deps succeeded).

list_all() is called once and the succeeded_ids set is shared across
all dispatch attempts — eliminates the O(N²) repository scan that would
occur if each TaskAssignUseCase.execute() loaded the full task list
independently.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from src.domain import TaskStatus
from src.domain.repositories import TaskRepositoryPort
from src.app.usecases.task_assign import TaskAssignUseCase, AssignOutcome

log = structlog.get_logger(__name__)


@dataclass
class TaskUnblockResult:
    completed_task_id: str
    unblocked: list[str] = field(default_factory=list)   # task_ids that were assigned
    skipped:   list[str] = field(default_factory=list)   # found dependent but deps still unmet

    @property
    def count(self) -> int:
        return len(self.unblocked)


class TaskUnblockUseCase:
    """
    Scan for tasks that depend on completed_task_id and assign any
    that are now fully unblocked.

    Delegates the actual assignment to TaskAssignUseCase, passing the
    preloaded succeeded_ids set to avoid redundant list_all() calls.
    """

    def __init__(
        self,
        task_repo: TaskRepositoryPort,
        assign_usecase: TaskAssignUseCase,
    ) -> None:
        self._repo   = task_repo
        self._assign = assign_usecase

    def execute(self, completed_task_id: str) -> TaskUnblockResult:
        log.info("task_unblock.scanning", completed=completed_task_id)

        all_tasks    = self._repo.list_all()
        succeeded_ids = {
            t.task_id for t in all_tasks
            if t.status == TaskStatus.SUCCEEDED
        }

        result = TaskUnblockResult(completed_task_id=completed_task_id)

        for task in all_tasks:
            if completed_task_id not in task.depends_on:
                continue

            if not task.is_ready_for_dispatch(succeeded_ids):
                result.skipped.append(task.task_id)
                continue

            log.info(
                "task_unblock.dispatching_dependent",
                task_id=task.task_id,
                completed=completed_task_id,
            )
            assign_result = self._assign.execute(
                task.task_id,
                preloaded_succeeded=succeeded_ids,
            )
            if assign_result.outcome == AssignOutcome.ASSIGNED:
                result.unblocked.append(task.task_id)
            else:
                result.skipped.append(task.task_id)

        return result
