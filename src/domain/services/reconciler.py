"""
src/domain/services/reconciler.py — Reconciliation domain service.

Encapsulates the pure domain logic of a single reconciliation assessment:
given a task, its current lease status, and its assigned agent, what action
should the system take?

This service is intentionally side-effect free. It returns a
ReconciliationDecision value object. The application engine
(app/reconciliation/reconciliation_engine.py) acts on that decision
by performing CAS writes and publishing events.

Split of responsibilities:
  ReconciliationService (domain)   — decides WHAT to do
  Reconciler (app engine)          — does it (CAS + events + loop)
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from src.domain.aggregates.task import TaskAggregate
from src.domain.entities.agent import AgentProps
from src.domain.rules.task_rules import TaskRules
from src.domain.value_objects.status import TaskStatus


class ReconciliationAction(str, Enum):
    """The action the reconciler should take for a given task."""

    NO_ACTION        = "no_action"
    REPUBLISH_PENDING = "republish_pending"   # task stuck in CREATED/REQUEUED
    FAIL_DEAD_AGENT  = "fail_dead_agent"      # assigned agent missed heartbeat
    FAIL_LEASE_EXPIRED = "fail_lease_expired" # lease expired on ASSIGNED or IN_PROGRESS
    WARN_NO_COMMIT   = "warn_no_commit"       # SUCCEEDED but no commit_sha (data quality)


@dataclass(frozen=True)
class ReconciliationDecision:
    """
    Immutable value object: the result of assessing a single task.

    action — what should happen
    reason — human-readable explanation, used as the failure message when
             action is FAIL_* and as a log annotation otherwise
    """

    action: ReconciliationAction
    reason: str = ""


# Sentinel for tasks that need no intervention
_NO_ACTION = ReconciliationDecision(action=ReconciliationAction.NO_ACTION)


class ReconciliationService:
    """
    Pure domain service: assesses one task per call.

    assess() is the only public method. It inspects the task state,
    compares it against the lease status and agent heartbeat, and returns
    a ReconciliationDecision that the application engine can act on.

    No ports, no side effects, no state.
    """

    def __init__(self, stuck_task_min_age_seconds: int = 120) -> None:
        self._stuck_age = stuck_task_min_age_seconds

    def assess(
        self,
        task: TaskAggregate,
        lease_active: bool,
        agent: Optional[AgentProps] = None,
    ) -> ReconciliationDecision:
        """
        Assess a single task and return the appropriate action.

        Parameters
        ----------
        task        : the task aggregate to inspect
        lease_active: whether the task's lease is currently active (from LeasePort)
        agent       : the AgentProps of the assigned agent, or None if not found

        Returns
        -------
        ReconciliationDecision — always returns a decision, never raises.
        """
        # -------------------------------------------------------------------
        # SUCCEEDED without commit_sha — data quality warning (check first,
        # before the terminal guard, since SUCCEEDED is in terminal())
        # -------------------------------------------------------------------
        if (
            task.status == TaskStatus.SUCCEEDED
            and task.result
            and not task.result.commit_sha
        ):
            return ReconciliationDecision(
                action=ReconciliationAction.WARN_NO_COMMIT,
                reason="Task succeeded but has no commit_sha",
            )

        # Terminal statuses are never acted on — the task manager owns them.
        if task.status in TaskStatus.terminal():
            return _NO_ACTION

        # -------------------------------------------------------------------
        # CREATED / REQUEUED — stuck pending assignment
        # -------------------------------------------------------------------
        if task.status in TaskStatus.assignable():
            if TaskRules.is_stuck_pending(task, self._stuck_age):
                event_type = (
                    "task.created"
                    if task.status == TaskStatus.CREATED
                    else "task.requeued"
                )
                return ReconciliationDecision(
                    action=ReconciliationAction.REPUBLISH_PENDING,
                    reason=event_type,
                )
            return _NO_ACTION

        # -------------------------------------------------------------------
        # ASSIGNED — check dead agent first, then lease expiry
        # -------------------------------------------------------------------
        if task.status == TaskStatus.ASSIGNED and task.assignment:
            if TaskRules.is_assigned_to_dead_agent(task, agent):
                return ReconciliationDecision(
                    action=ReconciliationAction.FAIL_DEAD_AGENT,
                    reason=f"Agent {task.assignment.agent_id} missed heartbeat threshold",
                )
            if TaskRules.is_lease_expired(task, lease_active):
                return ReconciliationDecision(
                    action=ReconciliationAction.FAIL_LEASE_EXPIRED,
                    reason="Lease expired while ASSIGNED",
                )

        # -------------------------------------------------------------------
        # IN_PROGRESS — expired lease means worker timed out or crashed
        # -------------------------------------------------------------------
        if task.status == TaskStatus.IN_PROGRESS:
            if TaskRules.is_lease_expired(task, lease_active):
                return ReconciliationDecision(
                    action=ReconciliationAction.FAIL_LEASE_EXPIRED,
                    reason="Lease expired while IN_PROGRESS",
                )

        return _NO_ACTION
