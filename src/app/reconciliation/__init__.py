"""
src/app/reconciliation/ — Reconciliation control-loop package.

A federated set of ``ControlLoop`` implementations — one per layer/aggregate —
run under a single ``ReconcilerScheduler`` (shared timing, telemetry, backoff,
single-writer guard). The legacy ``Reconciler`` remains as a task+PR facade for
established callers; new wiring composes the loops directly.
"""

from src.app.reconciliation.control_loop import ControlLoop, ReconcilerScheduler
from src.app.reconciliation.goal_pr_reconciler import GoalPRReconciler
from src.app.reconciliation.phase_dispatch_reconciler import PhaseDispatchReconciler
from src.app.reconciliation.reconciliation_engine import Reconciler
from src.app.reconciliation.task_reconciler import TaskReconciler

__all__ = [
    "ControlLoop",
    "ReconcilerScheduler",
    "TaskReconciler",
    "GoalPRReconciler",
    "PhaseDispatchReconciler",
    "Reconciler",
]
