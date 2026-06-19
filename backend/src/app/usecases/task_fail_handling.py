"""
src/app/usecases/task_fail_handling.py — Task failure handling use case.

Reacts to a task.failed event from either the worker or the reconciler
and applies the retry policy:

  needs_retry()  → requeue  → emit task.requeued
  needs_cancel() → cancel   → emit task.canceled
  neither        → stale event, discard silently

Uses optimistic CAS with retry so concurrent writes from the reconciler
do not cause spurious cancellations.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import structlog

from src.domain import DomainEvent
from src.domain.ports import EventPort
from src.domain.repositories import TaskRepositoryPort

log = structlog.get_logger(__name__)

PRODUCER = "agent-task-manager"
MAX_CAS_RETRIES = 5


class FailHandlingOutcome(str, Enum):
    REQUEUED  = "requeued"
    CANCELED  = "canceled"
    SKIPPED   = "skipped"    # stale event — task already moved on
    NOT_FOUND = "not_found"  # task deleted between events


@dataclass(frozen=True)
class TaskFailHandlingResult:
    outcome: FailHandlingOutcome
    task_id: str


class _VersionConflict(Exception):
    pass


class TaskFailHandlingUseCase:
    """
    Decide whether to requeue or cancel a failed task.

    Centralises retry/cancel policy in one place so both the worker
    failure path and the reconciler failure path land here identically.
    """

    def __init__(
        self,
        task_repo: TaskRepositoryPort,
        event_port: EventPort,
    ) -> None:
        self._repo   = task_repo
        self._events = event_port

    def execute(self, task_id: str) -> TaskFailHandlingResult:
        for attempt in range(MAX_CAS_RETRIES):
            try:
                return self._attempt(task_id)
            except _VersionConflict:
                log.warning(
                    "task_fail_handling.version_conflict",
                    task_id=task_id,
                    attempt=attempt,
                )
        log.error("task_fail_handling.max_retries_exhausted", task_id=task_id)
        return TaskFailHandlingResult(outcome=FailHandlingOutcome.SKIPPED, task_id=task_id)

    def _attempt(self, task_id: str) -> TaskFailHandlingResult:
        try:
            task = self._repo.load(task_id)
        except KeyError:
            log.warning("task_fail_handling.task_not_found", task_id=task_id)
            return TaskFailHandlingResult(outcome=FailHandlingOutcome.NOT_FOUND, task_id=task_id)

        # Stale event — task already moved on
        if not task.needs_retry() and not task.needs_cancel():
            log.info(
                "task_fail_handling.skip_not_failed",
                task_id=task_id,
                status=task.status.value,
            )
            return TaskFailHandlingResult(outcome=FailHandlingOutcome.SKIPPED, task_id=task_id)

        expected_v = task.state_version

        if task.needs_retry():
            task.requeue()
            if not self._repo.update_if_version(task_id, task, expected_v):
                raise _VersionConflict()

            self._events.publish(DomainEvent(
                type="task.requeued",
                producer=PRODUCER,
                payload={"task_id": task_id},
            ))
            log.info(
                "task_fail_handling.requeued",
                task_id=task_id,
                attempt=task.retry_policy.attempt,
                max_retries=task.retry_policy.max_retries,
            )
            return TaskFailHandlingResult(outcome=FailHandlingOutcome.REQUEUED, task_id=task_id)

        # Retries exhausted — cancel permanently
        task.cancel("Max retries exhausted")
        if not self._repo.update_if_version(task_id, task, expected_v):
            raise _VersionConflict()

        self._events.publish(DomainEvent(
            type="task.canceled",
            producer=PRODUCER,
            payload={"task_id": task_id, "reason": "max_retries_exhausted"},
        ))
        log.info(
            "task_fail_handling.canceled",
            task_id=task_id,
            attempts=task.retry_policy.attempt,
        )
        return TaskFailHandlingResult(outcome=FailHandlingOutcome.CANCELED, task_id=task_id)
