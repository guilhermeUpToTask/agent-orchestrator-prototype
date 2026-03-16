"""
src/app/reconciler.py — Reconciler loop (safety net only).

Architecture decision: the reconciler is NOT a scheduler.
It is a watchdog that detects silent failures and stuck tasks, then emits
the appropriate event so the task manager can react.

What it watches (polling every 60 s+):

  CREATED / REQUEUED, age > STUCK_TASK_MIN_AGE_SECONDS
      → republish task.created / task.requeued (crash-recovery for the
        no-eligible-agent dead end and post-crash orphans)
      → uses task.updated_at to gate: the event-driven path gets clear
        priority during the first pass window (~60 s); the reconciler only
        acts if assignment still has not happened after that window

  ASSIGNED  + dead agent        → _fail_task → task.failed
  ASSIGNED  + expired lease     → _fail_task → task.failed
  IN_PROGRESS + expired lease   → _fail_task → task.failed
  SUCCEEDED + no commit_sha     → warning only (no state change)

What it deliberately ignores:
  FAILED   — task manager receives task.failed and decides requeue/cancel
  CANCELED — terminal
  MERGED   — terminal
  SUCCEEDED (with sha) — healthy terminal

The minimum-age gate (STUCK_TASK_MIN_AGE_SECONDS, default 120 s) ensures
the reconciler never races against the task manager on a healthy run:

  t=0      task.created published by writer
  t≈0      task manager assigns, publishes task.assigned
  t=60     reconciler first pass — task is ASSIGNED, ignored
  (normal path — reconciler never touches this task)

  t=0      task.created published; task manager crashes
  t=60     reconciler first pass — task is CREATED, age 60 s < 120 s → skip
  t=120    reconciler second pass — age 120 s ≥ 120 s → republish task.created
  t≈120    task manager (restarted) picks it up
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import structlog

from src.core.models import DomainEvent, TaskStatus
from src.core.ports import AgentRegistryPort, EventPort, LeasePort, TaskRepositoryPort
from src.core.services import AnomalyDetectionService

log = structlog.get_logger(__name__)

MAX_CAS_RETRIES = 3

# A CREATED or REQUEUED task is only re-published if it has been in that
# state for at least this many seconds with no assignment.
# Must be comfortably longer than normal task-manager latency (typically <1 s)
# but short enough to recover quickly after a crash.
# Default: 2 × reconciler interval (2 × 60 = 120 s).
STUCK_TASK_MIN_AGE_SECONDS = 120

# Statuses the reconciler never acts on — task manager drives these via events.
_IGNORED_STATUSES = frozenset(
    {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELED,
        TaskStatus.MERGED,
    }
)


class Reconciler:
    """
    Watchdog process: scans task state periodically and either re-emits a
    pending event (crash recovery / no-agent dead end) or emits task.failed
    for tasks whose worker went silent.

    Does not implement retry or cancellation policy — those belong to the
    task manager, which reacts to task.failed.
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
        self._repo = task_repo
        self._lease = lease_port
        self._events = event_port
        self._registry = agent_registry
        self._interval = interval_seconds
        self._stuck_age = stuck_task_min_age_seconds

    # ------------------------------------------------------------------
    # Loop
    # ------------------------------------------------------------------

    def run_once(self) -> None:
        """Run a single reconciliation pass."""
        tasks = self._repo.list_all()
        active = [t for t in tasks if t.status not in _IGNORED_STATUSES]
        log.info(
            "reconciler.pass",
            total_tasks=len(tasks),
            active_tasks=len(active),
        )
        for task in active:
            try:
                self._reconcile_task(task)
            except Exception as exc:
                log.exception("reconciler.error", task_id=task.task_id, error=str(exc))

    def run_forever(self) -> None:
        """Block forever, running reconcile loop."""
        log.info(
            "reconciler.started",
            interval=self._interval,
            stuck_age=self._stuck_age,
        )
        while True:
            self.run_once()
            time.sleep(self._interval)

    # ------------------------------------------------------------------
    # Per-task logic
    # ------------------------------------------------------------------

    def _reconcile_task(self, task) -> None:
        # ------------------------------------------------------------------
        # CREATED / REQUEUED — task exists in YAML but has not been assigned.
        #
        # Two scenarios:
        #   1. Task manager crashed between ACKing the stream message and
        #      persisting the assignment (or before emitting the event at all).
        #   2. Task manager ran, found no eligible agent, returned False —
        #      and no agent has come online since.
        #
        # We only act if the task has been in this state for longer than
        # _stuck_age seconds.  This gives the event-driven path clear priority
        # on every healthy run while still recovering stuck tasks.
        # ------------------------------------------------------------------
        if task.status in (TaskStatus.CREATED, TaskStatus.REQUEUED):
            age = self._task_status_age_seconds(task)
            if AnomalyDetectionService.is_stuck_pending(task, self._stuck_age):
                log.warning(
                    "reconciler.stuck_pending_task",
                    task_id=task.task_id,
                    status=task.status.value,
                    age_seconds=round(age, 1),
                    threshold_seconds=self._stuck_age,
                )
                self._republish_pending(task)
            else:
                log.debug(
                    "reconciler.pending_task_not_yet_stuck",
                    task_id=task.task_id,
                    status=task.status.value,
                    age_seconds=round(age, 1),
                    threshold_seconds=self._stuck_age,
                )
            return

        lease_active = self._lease.is_lease_active(task.task_id)

        # ------------------------------------------------------------------
        # ASSIGNED — check for dead agent first, then lease expiry.
        # ------------------------------------------------------------------
        if task.status == TaskStatus.ASSIGNED and task.assignment:
            agent = self._registry.get(task.assignment.agent_id)
            if AnomalyDetectionService.is_assigned_to_dead_agent(task, agent):
                log.warning(
                    "reconciler.agent_dead",
                    task_id=task.task_id,
                    agent_id=task.assignment.agent_id,
                )
                self._fail_task(
                    task,
                    reason=f"Agent {task.assignment.agent_id} missed heartbeat threshold",
                )
                return

            if AnomalyDetectionService.is_lease_expired(task, lease_active):
                log.warning(
                    "reconciler.lease_expired_assigned",
                    task_id=task.task_id,
                    agent_id=task.assignment.agent_id,
                )
                self._fail_task(task, reason="Lease expired while ASSIGNED")
                return

        # ------------------------------------------------------------------
        # IN_PROGRESS — expired lease means the worker timed out or crashed.
        # ------------------------------------------------------------------
        if task.status == TaskStatus.IN_PROGRESS:
            if AnomalyDetectionService.is_lease_expired(task, lease_active):
                log.warning(
                    "reconciler.lease_expired_in_progress",
                    task_id=task.task_id,
                )
                self._fail_task(task, reason="Lease expired while IN_PROGRESS")
                return

        # ------------------------------------------------------------------
        # SUCCEEDED without commit_sha — data quality warning, no state change.
        # ------------------------------------------------------------------
        if task.status == TaskStatus.SUCCEEDED and task.result and not task.result.commit_sha:
            log.warning("reconciler.succeeded_no_commit", task_id=task.task_id)

    # ------------------------------------------------------------------
    # _republish_pending — re-emit task.created / task.requeued
    # ------------------------------------------------------------------

    def _republish_pending(self, task) -> None:
        """
        Re-emit the event for a task stuck in CREATED or REQUEUED.

        This does NOT modify task state — it only re-publishes the event
        that the task manager should have consumed.  The task manager will
        attempt assignment on receipt; if it succeeds, the task moves to
        ASSIGNED and will not be republished on the next pass.  If no
        eligible agent exists, the task remains CREATED/REQUEUED and will
        be republished again on the next reconciler pass after _stuck_age.
        """
        event_type = "task.created" if task.status == TaskStatus.CREATED else "task.requeued"
        self._events.publish(
            DomainEvent(
                type=event_type,
                producer="reconciler",
                payload={"task_id": task.task_id},
            )
        )
        log.info(
            "reconciler.republished_pending",
            task_id=task.task_id,
            event_type=event_type,  # "event" is reserved by structlog
        )

    # ------------------------------------------------------------------
    # _fail_task — emits task.failed; task manager decides what follows
    # ------------------------------------------------------------------

    def _fail_task(self, task, reason: str) -> None:
        """
        Transition the task to FAILED and emit task.failed.

        The task manager subscribes to task.failed and decides whether to
        requeue (retries remaining) or cancel (retries exhausted).

        Uses optimistic CAS.  If the task has already moved out of the
        active state (worker recovered and completed just before we acted),
        we silently return — no spurious failure is written.
        """
        log.info("reconciler.failing_task", task_id=task.task_id, reason=reason)
        for attempt in range(MAX_CAS_RETRIES):
            fresh = self._repo.load(task.task_id)

            # Guard: only fail tasks still in an active state.
            if fresh.status not in (TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS):
                log.info(
                    "reconciler.fail_skipped_status_changed",
                    task_id=task.task_id,
                    current_status=fresh.status.value,
                )
                return

            expected_v = fresh.state_version
            fresh.fail(reason)
            if self._repo.update_if_version(task.task_id, fresh, expected_v):
                self._events.publish(
                    DomainEvent(
                        type="task.failed",
                        producer="reconciler",
                        payload={"task_id": task.task_id, "reason": reason},
                    )
                )
                log.info(
                    "reconciler.task_failed",
                    task_id=task.task_id,
                    attempt=attempt,
                    reason=reason,
                )
                return

            log.warning(
                "reconciler.fail_cas_conflict",
                task_id=task.task_id,
                attempt=attempt,
            )

        log.error("reconciler.fail_cas_exhausted", task_id=task.task_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _task_status_age_seconds(task) -> float:
        """
        Seconds since the task last changed state, based on updated_at.

        updated_at is stamped by TaskAggregate._bump() on every transition,
        so it accurately reflects when the task entered its *current* status:
          - CREATED:  equals created_at (no transitions yet)
          - REQUEUED: set when requeue() was called
        """
        now = datetime.now(timezone.utc)
        return (now - task.updated_at).total_seconds()
