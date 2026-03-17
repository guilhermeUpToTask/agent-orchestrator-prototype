"""
src/app/usecases/task_retry.py — Task retry (operator override) use case.

Extracted from the `orchestrator task retry` CLI command as part of
Phase 4 refactoring.  The CLI should only parse args, create the
container, and call an application service — not contain workflow logic.

This use case encapsulates the "operator forces a task back to REQUEUED"
workflow:
  1. Load task by ID (raises if not found)
  2. Apply force_requeue() on the aggregate (domain enforces invariants)
  3. Persist unconditionally (operator action, no CAS race expected)
  4. Publish task.requeued event

The retry counter is NOT incremented — this is an explicit operator action,
not an automatic retry.  The domain aggregate records the previous status
in its history so operators can audit what happened.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from src.core.models import DomainEvent, TaskStatus
from src.core.ports import EventPort, TaskRepositoryPort

log = structlog.get_logger(__name__)


@dataclass
class TaskRetryResult:
    task_id: str
    previous_status: TaskStatus


class TaskRetryUseCase:
    """
    Application use case: force-requeue a task regardless of its current status.

    Keeps all workflow coordination out of the CLI layer.
    """

    def __init__(
        self,
        task_repo: TaskRepositoryPort,
        event_port: EventPort,
    ) -> None:
        self._repo = task_repo
        self._events = event_port

    def execute(self, task_id: str, actor: str = "cli:task-retry") -> TaskRetryResult:
        """
        Force-requeue task_id.

        Returns TaskRetryResult with the previous status for reporting.
        Raises KeyError if the task does not exist.
        Raises ValueError if the task is in MERGED status (domain invariant).
        """
        task = self._repo.load(task_id)   # raises KeyError if not found
        previous = task.status

        # Domain aggregate enforces the invariant (blocks MERGED)
        task.force_requeue(actor=actor)

        # Unconditional save — operator override intentionally bypasses CAS
        self._repo.save(task)

        self._events.publish(
            DomainEvent(
                type="task.requeued",
                producer=actor,
                payload={"task_id": task_id},
            )
        )

        log.info(
            "task_retry.force_requeued",
            task_id=task_id,
            previous_status=previous.value,
            actor=actor,
        )
        return TaskRetryResult(task_id=task_id, previous_status=previous)
