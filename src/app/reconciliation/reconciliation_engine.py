"""
src/app/reconciliation/reconciliation_engine.py — Reconciler application engine.

Owns the poll loop, CAS writes, and event publishing.
All domain decisions are delegated to ReconciliationService.

Split of responsibilities:
  ReconciliationService (src/domain/services/reconciler.py)
      Pure domain: given a task + context → ReconciliationDecision
  Reconciler (this file)
      Application: loop + act on decision (CAS + events + sleep)
"""
from __future__ import annotations

import time

import structlog

from src.domain import (
    DomainEvent,
    ReconciliationAction,
    ReconciliationService,
    TaskStatus,
)
from src.domain import AgentRegistryPort, EventPort, LeasePort, TaskRepositoryPort

log = structlog.get_logger(__name__)

MAX_CAS_RETRIES = 3
STUCK_TASK_MIN_AGE_SECONDS = 120


class Reconciler:
    """
    Application-layer watchdog loop.

    On each pass: loads all active tasks, asks ReconciliationService what
    to do with each one, then acts: re-publishes events or fails the task
    via CAS write + event emission.
    """

    def __init__(
        self,
        task_repo: TaskRepositoryPort,
        lease_port: LeasePort,
        event_port: EventPort,
        agent_registry: AgentRegistryPort,
        interval_seconds: int = 60,
        stuck_task_min_age_seconds: int = STUCK_TASK_MIN_AGE_SECONDS,
    ) -> None:
        self._repo     = task_repo
        self._lease    = lease_port
        self._events   = event_port
        self._registry = agent_registry
        self._interval = interval_seconds
        self._svc      = ReconciliationService(
            stuck_task_min_age_seconds=stuck_task_min_age_seconds
        )

    # ------------------------------------------------------------------
    # Loop
    # ------------------------------------------------------------------

    def run_once(self) -> None:
        """Run a single reconciliation pass."""
        tasks  = self._repo.list_all()
        active = [t for t in tasks if t.status not in TaskStatus.terminal()]
        log.info("reconciler.pass", total_tasks=len(tasks), active_tasks=len(active))
        for task in active:
            try:
                self._process(task)
            except Exception as exc:
                log.exception("reconciler.error", task_id=task.task_id, error=str(exc))

    def run_forever(self) -> None:
        """Block forever, running the reconcile loop."""
        log.info("reconciler.started", interval=self._interval)
        while True:
            self.run_once()
            time.sleep(self._interval)

    # ------------------------------------------------------------------
    # Per-task: assess via domain service → act on decision
    # ------------------------------------------------------------------

    def _process(self, task) -> None:
        lease_active = self._lease.is_lease_active(task.task_id)
        agent = (
            self._registry.get(task.assignment.agent_id)
            if task.assignment
            else None
        )

        decision = self._svc.assess(task, lease_active, agent)

        if decision.action == ReconciliationAction.NO_ACTION:
            return

        if decision.action == ReconciliationAction.REPUBLISH_PENDING:
            self._republish(task, event_type=decision.reason)
            return

        if decision.action == ReconciliationAction.WARN_NO_COMMIT:
            log.warning("reconciler.succeeded_no_commit", task_id=task.task_id)
            return

        if decision.action in (
            ReconciliationAction.FAIL_DEAD_AGENT,
            ReconciliationAction.FAIL_LEASE_EXPIRED,
        ):
            log.warning(
                f"reconciler.{decision.action.value}",
                task_id=task.task_id,
                reason=decision.reason,
            )
            self._fail(task, reason=decision.reason)

    # ------------------------------------------------------------------
    # Side effects
    # ------------------------------------------------------------------

    def _republish(self, task, event_type: str) -> None:
        """Re-emit task.created or task.requeued without modifying state."""
        self._events.publish(DomainEvent(
            type=event_type,
            producer="reconciler",
            payload={"task_id": task.task_id},
        ))
        log.info("reconciler.republished_pending",
                 task_id=task.task_id, event_type=event_type)

    def _fail(self, task, reason: str) -> None:
        """
        Transition task → FAILED via CAS write, then emit task.failed.
        If the task moved on before we act (worker recovered), skip silently.
        """
        log.info("reconciler.failing_task", task_id=task.task_id, reason=reason)
        for attempt in range(MAX_CAS_RETRIES):
            fresh = self._repo.load(task.task_id)
            if fresh.status not in TaskStatus.active():
                log.info("reconciler.fail_skipped_status_changed",
                         task_id=task.task_id, current_status=fresh.status.value)
                return
            expected_v = fresh.state_version
            fresh.fail(reason)
            if self._repo.update_if_version(task.task_id, fresh, expected_v):
                self._events.publish(DomainEvent(
                    type="task.failed",
                    producer="reconciler",
                    payload={"task_id": task.task_id, "reason": reason},
                ))
                log.info("reconciler.task_failed",
                         task_id=task.task_id, attempt=attempt, reason=reason)
                return
            log.warning("reconciler.fail_cas_conflict",
                        task_id=task.task_id, attempt=attempt)
        log.error("reconciler.fail_cas_exhausted", task_id=task.task_id)
