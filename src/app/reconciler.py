"""
src/app/reconciler.py — Reconciler loop.

Periodically scans workflow/tasks/*.yaml and:
  - Expired lease + ASSIGNED  → requeue (if retries remain) or cancel
  - Expired lease + IN_PROGRESS → fail
  - SUCCEEDED but no commit_sha → mark needs_attention
  - FAILED with retries left → requeue
"""
from __future__ import annotations

import time

import structlog

from src.core.models import DomainEvent, TaskStatus
from src.core.ports import EventPort, LeasePort, TaskRepositoryPort
from src.core.services import LeaseService

log = structlog.get_logger(__name__)

MAX_CAS_RETRIES = 3


class Reconciler:

    def __init__(
        self,
        task_repo: TaskRepositoryPort,
        lease_port: LeasePort,
        event_port: EventPort,
        interval_seconds: int = 30,
    ) -> None:
        self._repo = task_repo
        self._lease = lease_port
        self._events = event_port
        self._interval = interval_seconds

    def run_once(self) -> None:
        """Run a single reconciliation pass."""
        tasks = self._repo.list_all()
        log.info("reconciler.pass", total_tasks=len(tasks))

        for task in tasks:
            try:
                self._reconcile_task(task)
            except Exception as exc:
                log.exception("reconciler.error", task_id=task.task_id, error=str(exc))

    def run_forever(self) -> None:
        """Block forever, running reconcile loop."""
        log.info("reconciler.started", interval=self._interval)
        while True:
            self.run_once()
            time.sleep(self._interval)

    # ------------------------------------------------------------------
    # Per-task reconciliation
    # ------------------------------------------------------------------

    def _reconcile_task(self, task) -> None:
        lease_active = self._lease.is_lease_active(task.task_id)

        if LeaseService.should_requeue(task, lease_active):
            self._requeue_task(task)
        elif LeaseService.should_fail_stale(task, lease_active):
            self._fail_stale_task(task)
        elif (
            task.status == TaskStatus.FAILED
            and task.retry_policy.attempt < task.retry_policy.max_retries
        ):
            self._requeue_task(task)
        elif (
            task.status == TaskStatus.SUCCEEDED
            and task.result
            and not task.result.commit_sha
        ):
            log.warning("reconciler.succeeded_no_commit", task_id=task.task_id)

    def _requeue_task(self, task) -> None:
        log.info("reconciler.requeuing", task_id=task.task_id, status=task.status.value)
        for _ in range(MAX_CAS_RETRIES):
            fresh = self._repo.load(task.task_id)
            expected_v = fresh.state_version
            try:
                fresh.requeue()
            except ValueError as exc:
                log.warning("reconciler.requeue_skipped", task_id=task.task_id, reason=str(exc))
                return
            ok = self._repo.update_if_version(task.task_id, fresh, expected_v)
            if ok:
                self._events.publish(DomainEvent(
                    type="task.requeued",
                    producer="reconciler",
                    payload={"task_id": task.task_id},
                ))
                return
        log.error("reconciler.requeue_cas_failed", task_id=task.task_id)

    def _fail_stale_task(self, task) -> None:
        log.warning("reconciler.failing_stale", task_id=task.task_id)
        for _ in range(MAX_CAS_RETRIES):
            fresh = self._repo.load(task.task_id)
            expected_v = fresh.state_version
            fresh.fail("Stale: lease expired while in_progress")
            ok = self._repo.update_if_version(task.task_id, fresh, expected_v)
            if ok:
                self._events.publish(DomainEvent(
                    type="task.failed",
                    producer="reconciler",
                    payload={"task_id": task.task_id, "reason": "lease_expired"},
                ))
                return
        log.error("reconciler.fail_stale_cas_failed", task_id=task.task_id)
