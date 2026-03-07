"""
src/app/reconciler.py — Reconciler loop.

Periodically scans workflow/tasks/*.yaml and:
  - CREATED / REQUEUED with no active work → re-emit event (crash recovery)
  - ASSIGNED + dead agent                  → requeue immediately
  - ASSIGNED + expired lease               → requeue (if retries remain) or cancel
  - IN_PROGRESS + expired lease            → fail → reconciler requeues on next pass
  - FAILED with retries left               → requeue
  - SUCCEEDED but no commit_sha            → warn
"""
from __future__ import annotations

import time

import structlog

from src.core.models import DomainEvent, TaskStatus
from src.core.ports import AgentRegistryPort, EventPort, LeasePort, TaskRepositoryPort
from src.core.services import LeaseService, _is_alive

log = structlog.get_logger(__name__)

MAX_CAS_RETRIES = 3


class Reconciler:

    def __init__(
        self,
        task_repo: TaskRepositoryPort,
        lease_port: LeasePort,
        event_port: EventPort,
        agent_registry: AgentRegistryPort,
        interval_seconds: int = 30,
    ) -> None:
        self._repo = task_repo
        self._lease = lease_port
        self._events = event_port
        self._registry = agent_registry
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
        # ----------------------------------------------------------------
        # CREATED / REQUEUED — task exists in YAML but event was never
        # consumed (crash recovery). Re-emit to restart the flow.
        # ----------------------------------------------------------------
        if task.status in (TaskStatus.CREATED, TaskStatus.REQUEUED):
            self._republish_pending_task(task)
            return

        lease_active = self._lease.is_lease_active(task.task_id)

        # ----------------------------------------------------------------
        # ASSIGNED — check for dead agent before waiting for lease to expire
        # ----------------------------------------------------------------
        if task.status == TaskStatus.ASSIGNED and task.assignment:
            agent = self._registry.get(task.assignment.agent_id)
            if agent is not None and not _is_alive(agent):
                if task.retry_policy.attempt < task.retry_policy.max_retries:
                    log.warning(
                        "reconciler.agent_dead_requeuing",
                        task_id=task.task_id,
                        agent_id=task.assignment.agent_id,
                    )
                    self._requeue_task(task)
                else:
                    log.error(
                        "reconciler.agent_dead_retries_exhausted",
                        task_id=task.task_id,
                        agent_id=task.assignment.agent_id,
                        attempts=task.retry_policy.attempt,
                    )
                    self._cancel_task(task, reason="Agent dead and max retries exhausted")
                return

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
            task.status == TaskStatus.FAILED
            and task.retry_policy.attempt >= task.retry_policy.max_retries
        ):
            # Retries exhausted — cancel the task so it stops being processed
            # and is clearly marked as terminal.
            log.error(
                "reconciler.retries_exhausted",
                task_id=task.task_id,
                attempts=task.retry_policy.attempt,
                max_retries=task.retry_policy.max_retries,
            )
            self._cancel_task(task, reason="Max retries exhausted")
        elif (
            task.status == TaskStatus.SUCCEEDED
            and task.result
            and not task.result.commit_sha
        ):
            log.warning("reconciler.succeeded_no_commit", task_id=task.task_id)

    def _republish_pending_task(self, task) -> None:
        """
        Re-emit task.created or task.requeued so the task manager can
        pick them up again after a crash. The YAML is the source of truth —
        if the file says CREATED/REQUEUED, work needs to happen.
        """
        event_type = "task.created" if task.status == TaskStatus.CREATED else "task.requeued"
        log.info(
            "reconciler.republishing_pending",
            task_id=task.task_id,
            status=task.status.value,
        )
        self._events.publish(DomainEvent(
            type=event_type,
            producer="reconciler",
            payload={"task_id": task.task_id},
        ))

    def _requeue_task(self, task) -> None:
        log.info("reconciler.requeuing", task_id=task.task_id, status=task.status.value)
        for _ in range(MAX_CAS_RETRIES):
            fresh = self._repo.load(task.task_id)
            expected_v = fresh.state_version

            try:
                # requeue() only accepts FAILED → REQUEUED.
                # If the task is ASSIGNED or IN_PROGRESS (lease expired / agent
                # died), persist FAILED first so the audit trail is complete,
                # then requeue in a second write.
                if fresh.status in (TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS):
                    fresh.fail("Reconciler: lease expired or agent died before completion")
                    ok = self._repo.update_if_version(task.task_id, fresh, expected_v)
                    if not ok:
                        continue  # version conflict — retry outer loop
                    expected_v = fresh.state_version
                    log.info("reconciler.task_failed", task_id=task.task_id)

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

    def _cancel_task(self, task, reason: str) -> None:
        """
        Terminal state for tasks that have exhausted all retries.
        Writes CANCELED to disk and emits task.canceled so observers know
        this task will never be retried again.
        """
        log.error("reconciler.canceling", task_id=task.task_id, reason=reason)
        for _ in range(MAX_CAS_RETRIES):
            fresh = self._repo.load(task.task_id)
            # Don't cancel already-terminal tasks
            if fresh.status in (TaskStatus.CANCELED, TaskStatus.SUCCEEDED, TaskStatus.MERGED):
                return
            expected_v = fresh.state_version
            # fail() first if still in an active state before canceling
            if fresh.status in (TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS):
                fresh.fail(reason)
                self._repo.update_if_version(task.task_id, fresh, expected_v)
                expected_v = fresh.state_version
            fresh.cancel(reason)
            ok = self._repo.update_if_version(task.task_id, fresh, expected_v)
            if ok:
                self._events.publish(DomainEvent(
                    type="task.canceled",
                    producer="reconciler",
                    payload={"task_id": task.task_id, "reason": reason},
                ))
                return
        log.error("reconciler.cancel_cas_failed", task_id=task.task_id)