"""
src/app/handlers/task_manager.py — Task Manager: pure event router.

Receives domain events from the Redis Streams consumer loop and routes
each one to the appropriate use case. Contains no workflow logic itself.

Event → Use case mapping:
  task.created   → TaskAssignUseCase
  task.requeued  → TaskAssignUseCase
  task.completed → TaskUnblockUseCase
  task.failed    → TaskFailHandlingUseCase
"""
from __future__ import annotations

import structlog

from src.domain import SchedulerService
from src.domain.ports import EventPort, LeasePort
from src.domain.repositories import AgentRegistryPort, TaskRepositoryPort
from src.app.usecases.task_assign       import TaskAssignUseCase
from src.app.usecases.task_fail_handling import TaskFailHandlingUseCase
from src.app.usecases.task_unblock      import TaskUnblockUseCase

log = structlog.get_logger(__name__)


class TaskManagerHandler:
    """
    Pure event router for the task manager process.

    Each handle_* method maps one-to-one with an event type. All
    workflow logic lives in the use cases; this class only wires them
    together and routes events to the right one.
    """

    def __init__(
        self,
        task_repo: TaskRepositoryPort,
        agent_registry: AgentRegistryPort,
        event_port: EventPort,
        lease_port: LeasePort,
        scheduler: SchedulerService | None = None,
    ) -> None:
        assign = TaskAssignUseCase(
            task_repo=task_repo,
            agent_registry=agent_registry,
            event_port=event_port,
            lease_port=lease_port,
            scheduler=scheduler or SchedulerService(),
        )
        self._assign  = assign
        self._unblock = TaskUnblockUseCase(task_repo=task_repo, assign_usecase=assign)
        self._fail    = TaskFailHandlingUseCase(task_repo=task_repo, event_port=event_port)

    def handle_task_created(self, task_id: str) -> bool:
        return self._handle_assignable_task(task_id, event_type="task.created")

    def handle_task_requeued(self, task_id: str) -> bool:
        return self._handle_assignable_task(task_id, event_type="task.requeued")

    def _handle_assignable_task(self, task_id: str, event_type: str) -> bool:
        log.info("task_manager.handling_assignable", task_id=task_id, event_type=event_type)
        from src.app.usecases.task_assign import AssignOutcome
        result = self._assign.execute(task_id)
        return result.outcome == AssignOutcome.ASSIGNED

    def handle_task_completed(self, completed_task_id: str) -> None:
        log.info("task_manager.handling_completed", task_id=completed_task_id)
        self._unblock.execute(completed_task_id)

    def handle_task_failed(self, task_id: str) -> None:
        log.info("task_manager.handling_failed", task_id=task_id)
        self._fail.execute(task_id)
