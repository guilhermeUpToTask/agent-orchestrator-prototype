"""
src/app/handlers/task_manager.py — Task Manager use-case handler.

The Task Manager is itself an agent (capabilities include 'task_manager').
It:
  1. Listens for task.created / task.requeued / task.completed events
  2. Selects an agent via SchedulerService
  3. Persists assignment (update_if_version)
  4. Creates a lease
  5. Publishes task.assigned  ← this is what wakes the worker up
"""
from __future__ import annotations

from uuid import uuid4

import structlog

from src.core.models import Assignment, DomainEvent, TaskAggregate, TaskStatus
from src.core.ports import (
    AgentRegistryPort,
    EventPort,
    LeasePort,
    TaskRepositoryPort,
)
from src.core.services import SchedulerService

log = structlog.get_logger(__name__)

TASK_MANAGER_AGENT_ID = "agent-task-manager"
MAX_ASSIGN_RETRIES = 5


class TaskManagerHandler:
    """
    Processes task.created / task.requeued / task.completed events.
    Assigns tasks to agents and unblocks dependents on completion.
    Implements optimistic concurrency with retry on version conflict.
    """

    def __init__(
        self,
        task_repo: TaskRepositoryPort,
        agent_registry: AgentRegistryPort,
        event_port: EventPort,
        lease_port: LeasePort,
        scheduler: SchedulerService | None = None,
    ) -> None:
        self._repo = task_repo
        self._registry = agent_registry
        self._events = event_port
        self._lease = lease_port
        self._scheduler = scheduler or SchedulerService()

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def handle_task_created(self, task_id: str) -> bool:
        """
        Attempt to assign task_id.
        Returns True on successful assignment, False if no eligible agent.
        Raises on unrecoverable error.
        """
        log.info("task_manager.handling_created", task_id=task_id)
        return self._assign_with_retry(task_id)

    def handle_task_requeued(self, task_id: str) -> bool:
        log.info("task_manager.handling_requeued", task_id=task_id)
        return self._assign_with_retry(task_id)

    def handle_task_completed(self, completed_task_id: str) -> None:
        """
        When a task completes, check if any dependent tasks are now unblocked.
        A dependent task is dispatched only when ALL of its depends_on tasks
        have SUCCEEDED.
        """
        log.info("task_manager.handling_completed", task_id=completed_task_id)

        all_tasks = self._repo.list_all()
        succeeded_ids = {t.task_id for t in all_tasks if t.status == TaskStatus.SUCCEEDED}

        for task in all_tasks:
            if completed_task_id not in task.depends_on:
                continue
            if task.status != TaskStatus.CREATED:
                continue
            # All dependencies must be satisfied before dispatching
            if all(dep in succeeded_ids for dep in task.depends_on):
                log.info(
                    "task_manager.unblocking_dependent",
                    task_id=task.task_id,
                    completed=completed_task_id,
                )
                self._assign_with_retry(task.task_id)

    # ------------------------------------------------------------------
    # Internal logic
    # ------------------------------------------------------------------

    def _assign_with_retry(self, task_id: str) -> bool:
        for attempt in range(MAX_ASSIGN_RETRIES):
            try:
                return self._try_assign(task_id)
            except _VersionConflict:
                log.warning(
                    "task_manager.version_conflict",
                    task_id=task_id,
                    attempt=attempt,
                )
        log.error("task_manager.assign_failed_max_retries", task_id=task_id)
        return False

    def _try_assign(self, task_id: str) -> bool:
        task = self._repo.load(task_id)

        if task.status not in (TaskStatus.CREATED, TaskStatus.REQUEUED):
            log.info(
                "task_manager.skip_not_assignable",
                task_id=task_id,
                status=task.status,
            )
            return False

        # Do not dispatch tasks whose dependencies are not yet satisfied
        if task.depends_on:
            all_tasks = self._repo.list_all()
            succeeded_ids = {t.task_id for t in all_tasks if t.status == TaskStatus.SUCCEEDED}
            unmet = [dep for dep in task.depends_on if dep not in succeeded_ids]
            if unmet:
                log.info(
                    "task_manager.skip_unmet_dependencies",
                    task_id=task_id,
                    unmet=unmet,
                )
                return False

        agents = self._registry.list_agents()
        agent = self._scheduler.select_agent(task, agents)

        if agent is None:
            log.warning("task_manager.no_eligible_agent", task_id=task_id)
            return False

        expected_version = task.state_version
        assignment = Assignment(
            agent_id=agent.agent_id,
            lease_seconds=300,
        )

        # Persist assignment BEFORE emitting event (persist-first rule)
        task.assign(assignment)
        success = self._repo.update_if_version(task_id, task, expected_version)
        if not success:
            raise _VersionConflict()

        # Create lease
        lease_token = self._lease.create_lease(
            task_id=task_id,
            agent_id=agent.agent_id,
            lease_seconds=assignment.lease_seconds,
        )
        # Persist lease token into assignment (non-version-bumping update)
        task.assignment.lease_token = lease_token
        self._repo.save(task)

        # Emit task.assigned — this is what wakes the worker up.
        # The worker subscribes to this event via Redis Streams consumer groups.
        self._events.publish(
            DomainEvent(
                type="task.assigned",
                producer=TASK_MANAGER_AGENT_ID,
                correlation_id=task.feature_id,
                payload={
                    "task_id": task_id,
                    "agent_id": agent.agent_id,
                    "project_id": task.feature_id,
                },
            )
        )

        log.info(
            "task_manager.assigned",
            task_id=task_id,
            agent_id=agent.agent_id,
            lease_token=lease_token,
        )
        return True


class _VersionConflict(Exception):
    pass