"""
src/app/reconciliation/task_reconciler.py — Task watchdog control loop.

Reconciles in-flight tasks: detects dead agents and expired leases (→ FAIL via
CAS), and re-publishes pending tasks the task manager may have missed. All
per-task domain decisions are delegated to ``ReconciliationService.assess``;
this loop owns only the pass + side effects (CAS writes + events).
"""
from __future__ import annotations

import structlog

from src.app.reconciliation.control_loop import ControlLoop
from src.domain import (
    AgentRegistryPort,
    DomainEvent,
    EventPort,
    LeasePort,
    ReconciliationAction,
    ReconciliationService,
    TaskAggregate,
    TaskRepositoryPort,
    TaskStatus,
)

log = structlog.get_logger(__name__)

MAX_CAS_RETRIES = 3
STUCK_TASK_MIN_AGE_SECONDS = 120


class TaskReconciler(ControlLoop):
    """Application-layer watchdog over active tasks."""

    name = "tasks"

    def __init__(
        self,
        task_repo: TaskRepositoryPort,
        lease_port: LeasePort,
        event_port: EventPort,
        agent_registry: AgentRegistryPort,
        interval_seconds: int = 60,
        stuck_task_min_age_seconds: int = STUCK_TASK_MIN_AGE_SECONDS,
    ) -> None:
        self._repo = task_repo
        self._lease = lease_port
        self._events = event_port
        self._registry = agent_registry
        self.interval_seconds = interval_seconds
        self._svc = ReconciliationService(
            stuck_task_min_age_seconds=stuck_task_min_age_seconds
        )

    def reconcile_once(self) -> None:
        tasks = self._repo.list_all()
        active = [t for t in tasks if t.status not in TaskStatus.terminal()]
        log.info("reconciler.pass", total_tasks=len(tasks), active_tasks=len(active))
        for task in active:
            try:
                self._process(task)
            except Exception as exc:
                log.exception("reconciler.error", task_id=task.task_id, error=str(exc))

    # ------------------------------------------------------------------
    # Per-task: assess via domain service → act on decision
    # ------------------------------------------------------------------

    def _process(self, task: TaskAggregate) -> None:
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

        if decision.action == ReconciliationAction.RECLAIM_STALE:
            log.warning(
                "reconciler.reclaim_stale",
                task_id=task.task_id,
                reason=decision.reason,
            )
            self._reclaim(task, reason=decision.reason)

    # ------------------------------------------------------------------
    # Side effects
    # ------------------------------------------------------------------

    def _republish(self, task: TaskAggregate, event_type: str) -> None:
        """Re-emit task.created or task.requeued without modifying state."""
        self._events.publish(DomainEvent(
            type=event_type,
            producer="reconciler",
            payload={"task_id": task.task_id},
        ))
        log.info("reconciler.republished_pending",
                 task_id=task.task_id, event_type=event_type)

    def _reclaim(self, task: TaskAggregate, reason: str) -> None:
        """Liveness reclaim of a stale ASSIGNED/IN_PROGRESS task via CAS.

        A lapsed lease / dead agent means the worker is gone — so REQUEUE the
        task (it returns to the scheduler) rather than failing it, and revoke
        the stale lease so the old worker's refresher stops. This keeps infra
        liveness off the genuine-failure retry budget. Only once a task has
        churned past its reclaim budget do we fall back to failing it, so a
        genuinely unrunnable task still terminates instead of looping forever.
        """
        for attempt in range(MAX_CAS_RETRIES):
            fresh = self._repo.load(task.task_id)
            if fresh.status not in TaskStatus.active():
                log.info("reconciler.reclaim_skipped_status_changed",
                         task_id=task.task_id, current_status=fresh.status.value)
                return

            # Best-effort: drop the stale lease so the dead worker's refresher
            # can't keep it alive (no-op if it already expired).
            if fresh.assignment and fresh.assignment.lease_token:
                self._lease.revoke_lease(fresh.assignment.lease_token)

            expected_v = fresh.state_version
            if fresh.can_reclaim():
                fresh.reclaim(reason)
                event_type, event_payload = "task.requeued", {"task_id": task.task_id}
                done_log = ("reconciler.task_reclaimed",
                            {"reclaim_count": fresh.reclaim_count})
            else:
                fresh.fail(reason)  # reclaim budget exhausted → genuine failure path
                event_type, event_payload = (
                    "task.failed", {"task_id": task.task_id, "reason": reason},
                )
                done_log = ("reconciler.reclaim_budget_exhausted", {})

            if self._repo.update_if_version(task.task_id, fresh, expected_v):
                self._events.publish(DomainEvent(
                    type=event_type, producer="reconciler", payload=event_payload,
                ))
                log.info(done_log[0], task_id=task.task_id, reason=reason, **done_log[1])
                return
            log.warning("reconciler.reclaim_cas_conflict",
                        task_id=task.task_id, attempt=attempt)
        log.error("reconciler.reclaim_cas_exhausted", task_id=task.task_id)
