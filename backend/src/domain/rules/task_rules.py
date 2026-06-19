"""
src/domain/rules/task_rules.py — Task lifecycle business rules.

TaskRules is a stateless policy object. All methods are static and delegate
to the aggregate's own query methods — they add no new logic, only a stable
named surface that the application layer calls instead of inspecting aggregate
internals directly.

Replaces three former service classes:
  AnomalyDetectionService  → is_stuck_pending, is_lease_expired, is_assigned_to_dead_agent
  LifecyclePolicyService   → should_unblock_dependent, should_requeue_after_failure, should_cancel_after_failure
  LeaseService             → should_requeue_on_lease_expiry, should_fail_on_lease_expiry
"""
from __future__ import annotations

from typing import Optional

from src.domain.aggregates.task import TaskAggregate
from src.domain.entities.agent import AgentProps
from src.domain.value_objects.status import TaskStatus


class TaskRules:
    """
    Stateless policy rules for task lifecycle decisions.
    Use as a namespace — instantiation is optional but harmless.
    """

    # ------------------------------------------------------------------
    # Anomaly detection
    # ------------------------------------------------------------------

    @staticmethod
    def is_stuck_pending(task: TaskAggregate, threshold_seconds: int) -> bool:
        """Return True if the task has been waiting for assignment too long."""
        return task.is_stuck_pending(threshold_seconds)

    @staticmethod
    def is_lease_expired(task: TaskAggregate, lease_active: bool) -> bool:
        """Return True if an active task's lease has expired."""
        return task.is_lease_expired(lease_active)

    @staticmethod
    def is_assigned_to_dead_agent(
        task: TaskAggregate, agent: Optional[AgentProps]
    ) -> bool:
        """Return True if the assigned agent has missed its heartbeat threshold."""
        return task.is_assigned_to_dead_agent(agent)

    # ------------------------------------------------------------------
    # Lifecycle policy
    # ------------------------------------------------------------------

    @staticmethod
    def should_unblock_dependent(
        dependent: TaskAggregate,
        completed_task_ids: set[str],
    ) -> bool:
        """Return True if a dependent task is ready to be dispatched."""
        return dependent.is_ready_for_dispatch(completed_task_ids)

    @staticmethod
    def should_requeue_after_failure(task: TaskAggregate) -> bool:
        """Return True if the failed task should be automatically retried."""
        return task.needs_retry()

    @staticmethod
    def should_cancel_after_failure(task: TaskAggregate) -> bool:
        """Return True if the failed task should be permanently cancelled."""
        return task.needs_cancel()

    # ------------------------------------------------------------------
    # Lease policy
    # ------------------------------------------------------------------

    @staticmethod
    def should_requeue_on_lease_expiry(task: TaskAggregate, lease_active: bool) -> bool:
        """
        Return True if an ASSIGNED task with an expired lease still has retry
        budget and should be requeued rather than failed.
        """
        return (
            task.status == TaskStatus.ASSIGNED
            and not lease_active
            and task.retry_policy.can_retry()
        )

    @staticmethod
    def should_fail_on_lease_expiry(task: TaskAggregate, lease_active: bool) -> bool:
        """
        Return True if an IN_PROGRESS task with an expired lease should be
        failed — the worker timed out or crashed mid-execution.
        """
        return task.status == TaskStatus.IN_PROGRESS and not lease_active
