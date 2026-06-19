"""
src/app/reconciliation/reconciliation_engine.py — Reconciler facade.

Backward-compatible entry point. Historically a single watchdog over tasks +
PR polling; now a thin composition over the federated ControlLoops:

  * it IS the TaskReconciler (so ``reconciler._process`` and ``run_once`` keep
    working for existing callers/tests), and
  * it additionally schedules a GoalPRReconciler when PR dependencies are
    provided, under a shared ReconcilerScheduler.

New code should build a ``ReconcilerScheduler`` directly with the loops it needs
(see ``AppContainer.get_reconciler_scheduler``, which also wires the
PhaseDispatchReconciler). This class exists for the established constructor
contract and the ``run_once`` / ``run_forever`` / ``shutdown`` surface.

Re-exported here for stable import paths:
  MAX_CAS_RETRIES, STUCK_TASK_MIN_AGE_SECONDS
"""
from __future__ import annotations

from typing import Any, Optional

from src.app.reconciliation.control_loop import ControlLoop, ReconcilerScheduler
from src.app.reconciliation.goal_pr_reconciler import GoalPRReconciler
from src.app.reconciliation.task_reconciler import (
    MAX_CAS_RETRIES,
    STUCK_TASK_MIN_AGE_SECONDS,
    TaskReconciler,
)
from src.domain import AgentRegistryPort, EventPort, LeasePort, TaskRepositoryPort
from src.domain.repositories.goal_repository import GoalRepositoryPort

__all__ = ["Reconciler", "MAX_CAS_RETRIES", "STUCK_TASK_MIN_AGE_SECONDS"]


class Reconciler(TaskReconciler):
    """Task watchdog + optional PR polling, behind the legacy public API.

    Inherits the task pass from ``TaskReconciler`` (so ``self`` is the task
    ControlLoop and ``self._process`` remains the patchable seam), then composes
    a ``GoalPRReconciler`` and a ``ReconcilerScheduler`` to run both passes.
    """

    name = "tasks"

    def __init__(
        self,
        task_repo: TaskRepositoryPort,
        lease_port: LeasePort,
        event_port: EventPort,
        agent_registry: AgentRegistryPort,
        interval_seconds: int = 60,
        stuck_task_min_age_seconds: int = STUCK_TASK_MIN_AGE_SECONDS,
        # Optional PR-polling dependencies
        goal_repo: Optional[GoalRepositoryPort] = None,
        sync_pr_usecase: Any = None,
        advance_pr_usecase: Any = None,
    ) -> None:
        super().__init__(
            task_repo=task_repo,
            lease_port=lease_port,
            event_port=event_port,
            agent_registry=agent_registry,
            interval_seconds=interval_seconds,
            stuck_task_min_age_seconds=stuck_task_min_age_seconds,
        )

        loops: list[ControlLoop] = [self]
        self._pr_loop: Optional[GoalPRReconciler] = None
        if goal_repo is not None and sync_pr_usecase is not None and advance_pr_usecase is not None:
            self._pr_loop = GoalPRReconciler(
                goal_repo=goal_repo,
                sync_pr_usecase=sync_pr_usecase,
                advance_pr_usecase=advance_pr_usecase,
                interval_seconds=interval_seconds,
            )
            loops.append(self._pr_loop)

        self._scheduler = ReconcilerScheduler(loops)

    # ------------------------------------------------------------------
    # Legacy public API — delegates to the scheduler
    # ------------------------------------------------------------------

    def run_once(self) -> None:
        """Run a single reconciliation pass (tasks + PR polling)."""
        self._scheduler.run_once()

    def run_forever(self) -> None:
        """Run the reconcile loop until ``shutdown()`` is called."""
        self._scheduler.run_forever()

    def shutdown(self) -> None:
        """Unblock ``run_forever()`` at the next interval check."""
        self._scheduler.shutdown()
