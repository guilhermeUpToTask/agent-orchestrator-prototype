"""
src/app/handlers/task_manager.py — Task Manager use-case handler.

The Task Manager is itself an agent (capabilities include 'task_manager').
It:
  1. Listens for task.created / task.requeued events
  2. Selects an agent via SchedulerService
  3. Persists assignment (update_if_version)
  4. Creates a lease
  5. Publishes task.assigned
"""
from __future__ import annotations

import logging
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
    Processes task.created / task.requeued events and assigns tasks to agents.
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
    # Public entry point
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

        # Emit event (after persisting)
        self._events.publish(
            DomainEvent(
                type="task.assigned",
                producer=TASK_MANAGER_AGENT_ID,
                correlation_id=task.feature_id,
                payload={"task_id": task_id, "agent_id": agent.agent_id},
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
