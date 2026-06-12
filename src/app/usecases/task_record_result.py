"""
src/app/usecases/task_record_result.py — sole writer for execution outcomes.

Workers never write task state: they publish task.execution_started /
task.execution_succeeded / task.execution_failed with the result facts,
and the task manager (one process, this use case) applies the transitions.
That makes the file-backed CAS contract ("one writer process") actually
hold for task state — see the YamlTaskRepository docstring.

After persisting, the canonical notification events (task.started,
task.completed, task.failed) are published exactly as workers used to,
so downstream consumers (unblock, goal merge, retry handling, reconciler)
are untouched.

Every method is idempotent under event redelivery: a transition that has
already been applied (or has been overtaken by the reconciler) is skipped
with a log line, never an error.
"""
from __future__ import annotations

import structlog

from src.domain import DomainEvent, EventPort, TaskResult, TaskStatus
from src.domain import TaskRepositoryPort

log = structlog.get_logger(__name__)

MAX_CAS_RETRIES = 5
PRODUCER = "task-manager"


class TaskRecordResultUseCase:
    def __init__(self, task_repo: TaskRepositoryPort, event_port: EventPort) -> None:
        self._repo = task_repo
        self._events = event_port

    # ------------------------------------------------------------------
    # Transitions
    # ------------------------------------------------------------------

    def record_started(self, task_id: str, agent_id: str) -> None:
        """ASSIGNED → IN_PROGRESS, then publish task.started."""
        for attempt in range(MAX_CAS_RETRIES):
            task = self._load(task_id)
            if task is None:
                return
            if task.status != TaskStatus.ASSIGNED:
                log.info(
                    "task_record.start_skipped",
                    task_id=task_id,
                    status=task.status.value,
                )
                return
            if task.assignment is None or task.assignment.agent_id != agent_id:
                log.warning(
                    "task_record.start_skipped_assignment_changed",
                    task_id=task_id,
                    agent_id=agent_id,
                )
                return

            expected_v = task.state_version
            task.start()
            if self._repo.update_if_version(task_id, task, expected_v):
                self._events.publish(DomainEvent(
                    type="task.started",
                    producer=PRODUCER,
                    payload={"task_id": task_id, "agent_id": agent_id},
                ))
                return
            log.warning("task_record.start_cas_conflict", task_id=task_id, attempt=attempt)
        log.error("task_record.start_cas_exhausted", task_id=task_id)

    def record_succeeded(
        self, task_id: str, agent_id: str, result: TaskResult
    ) -> None:
        """(ASSIGNED|IN_PROGRESS) → SUCCEEDED, then publish task.completed.

        Accepts ASSIGNED because execution_started and execution_succeeded
        travel on different streams: a fast worker's success can arrive
        before its start. The start transition is applied as catch-up in
        the same write.
        """
        for attempt in range(MAX_CAS_RETRIES):
            task = self._load(task_id)
            if task is None:
                return
            if task.status == TaskStatus.SUCCEEDED:
                log.info("task_record.success_already_persisted", task_id=task_id)
                return
            if task.status not in TaskStatus.active():
                log.warning(
                    "task_record.success_skipped_not_active",
                    task_id=task_id,
                    status=task.status.value,
                )
                return

            expected_v = task.state_version
            if task.status == TaskStatus.ASSIGNED:
                task.start()
            task.complete(result)
            if self._repo.update_if_version(task_id, task, expected_v):
                self._events.publish(DomainEvent(
                    type="task.completed",
                    producer=PRODUCER,
                    payload={
                        "task_id": task_id,
                        "agent_id": agent_id,
                        "commit_sha": result.commit_sha,
                    },
                ))
                log.info(
                    "task_record.task_succeeded",
                    task_id=task_id,
                    commit_sha=result.commit_sha,
                )
                return
            log.warning("task_record.success_cas_conflict", task_id=task_id, attempt=attempt)
        log.error("task_record.success_cas_exhausted", task_id=task_id)

    def record_failed(self, task_id: str, agent_id: str, reason: str) -> None:
        """(ASSIGNED|IN_PROGRESS) → FAILED, then publish task.failed.

        Retry/cancel policy is NOT applied here — it stays with the
        task.failed consumer (TaskFailHandlingUseCase), which also covers
        reconciler-failed tasks through the same path.
        """
        for attempt in range(MAX_CAS_RETRIES):
            task = self._load(task_id)
            if task is None:
                return
            if task.status == TaskStatus.FAILED:
                log.info("task_record.failure_already_persisted", task_id=task_id)
                return
            if task.status not in TaskStatus.active():
                log.warning(
                    "task_record.failure_skipped_not_active",
                    task_id=task_id,
                    status=task.status.value,
                )
                return

            expected_v = task.state_version
            task.fail(reason)
            if self._repo.update_if_version(task_id, task, expected_v):
                self._events.publish(DomainEvent(
                    type="task.failed",
                    producer=PRODUCER,
                    payload={"task_id": task_id, "agent_id": agent_id, "reason": reason},
                ))
                log.error("task_record.task_failed", task_id=task_id, reason=reason)
                return
            log.warning("task_record.failure_cas_conflict", task_id=task_id, attempt=attempt)
        log.error("task_record.failure_cas_exhausted", task_id=task_id)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load(self, task_id: str):
        try:
            return self._repo.load(task_id)
        except KeyError:
            log.warning("task_record.task_gone", task_id=task_id)
            return None
