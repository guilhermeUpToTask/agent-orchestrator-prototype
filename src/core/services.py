"""
src/core/services.py — Domain services.
Pure business logic; no infra imports.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from src.core.models import AgentProps, TaskAggregate, TaskStatus


# ---------------------------------------------------------------------------
# Version comparison (simple semver subset: >=X.Y.Z)
# ---------------------------------------------------------------------------

def _parse_version(v: str) -> tuple[int, ...]:
    """Parse semver string like '1.2.3' into (1, 2, 3)."""
    parts = re.findall(r"\d+", v)
    return tuple(int(p) for p in parts[:3])


def _satisfies_version(agent_version: str, constraint: str) -> bool:
    """
    Supports '>=X.Y.Z' and exact 'X.Y.Z'.
    Returns True if agent_version satisfies constraint.
    """
    constraint = constraint.strip()
    if constraint.startswith(">="):
        required = _parse_version(constraint[2:])
        actual = _parse_version(agent_version)
        return actual >= required
    # Exact match fallback
    return _parse_version(agent_version) == _parse_version(constraint)


def _is_alive(agent: AgentProps, threshold_seconds: int = 60) -> bool:
    """
    Return True if the agent has sent a heartbeat within threshold_seconds.
    An agent with no heartbeat at all is considered dead.
    """
    if agent.last_heartbeat is None:
        return False
    age = (datetime.now(timezone.utc) - agent.last_heartbeat).total_seconds()
    return age < threshold_seconds


# ---------------------------------------------------------------------------
# SchedulerService — selects the best agent for a task
# ---------------------------------------------------------------------------

class SchedulerService:
    """
    Pure domain service. Selects the best available agent for a task.
    Scoring: capability match + version + available capacity.
    Only considers agents that are active AND alive (recent heartbeat).
    """

    def select_agent(
        self,
        task: TaskAggregate,
        agents: list[AgentProps],
    ) -> Optional[AgentProps]:
        """
        Returns the highest-scoring eligible agent, or None if no match.
        Eligibility:
          - active flag is True
          - has sent a heartbeat within the last 60 seconds
          - has required_capability in capabilities
          - satisfies min_version constraint
        """
        selector = task.agent_selector
        candidates = [
            a for a in agents
            if a.active
            and _is_alive(a)
            and selector.required_capability in a.capabilities
            and _satisfies_version(a.version, selector.min_version)
        ]
        if not candidates:
            return None

        # Score: prefer higher trust, higher max_concurrent, more matching tools
        def score(agent: AgentProps) -> tuple:
            trust_score = {"high": 3, "medium": 2, "low": 1}.get(agent.trust_level.value, 0)
            return (trust_score, agent.max_concurrent_tasks, len(agent.tools))

        return max(candidates, key=score)

    def eligible_agents(
        self,
        task: TaskAggregate,
        agents: list[AgentProps],
    ) -> list[AgentProps]:
        """Return all eligible agents without scoring."""
        selector = task.agent_selector
        return [
            a for a in agents
            if a.active
            and _is_alive(a)
            and selector.required_capability in a.capabilities
            and _satisfies_version(a.version, selector.min_version)
        ]


# ---------------------------------------------------------------------------
# LeaseService — domain logic for lease expiry decisions
# ---------------------------------------------------------------------------

class LeaseService:
    """
    Pure domain helper for lease-related decisions.
    (Redis operations are in LeasePort adapter.)
    """

    @staticmethod
    def should_requeue(task: TaskAggregate, lease_active: bool) -> bool:
        """
        Return True if task should be requeued because its lease expired.
        Only applies to ASSIGNED tasks.
        """

        return (
            task.status == TaskStatus.ASSIGNED
            and not lease_active
            and task.retry_policy.attempt < task.retry_policy.max_retries
        )

    @staticmethod
    def should_fail_stale(task: TaskAggregate, lease_active: bool) -> bool:
        """
        Return True if an IN_PROGRESS task with expired lease should be failed.
        """

        return (
            task.status == TaskStatus.IN_PROGRESS
            and not lease_active
        )

# ---------------------------------------------------------------------------
# AnomalyDetectionService — pure domain logic for system anomalies
# ---------------------------------------------------------------------------

class AnomalyDetectionService:
    """
    Pure domain service for identifying tasks in anomalous states.
    """

    @staticmethod
    def is_stuck_pending(task: TaskAggregate, threshold_seconds: int) -> bool:
        if task.status not in (TaskStatus.CREATED, TaskStatus.REQUEUED):
            return False
        age = (datetime.now(timezone.utc) - task.updated_at).total_seconds()
        return age >= threshold_seconds

    @staticmethod
    def is_assigned_to_dead_agent(task: TaskAggregate, agent: Optional[AgentProps]) -> bool:
        if task.status != TaskStatus.ASSIGNED or task.assignment is None:
            return False
        if agent is None:
            return False
        return not _is_alive(agent)

    @staticmethod
    def is_lease_expired(task: TaskAggregate, lease_active: bool) -> bool:
        if task.status not in (TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS):
            return False
        return not lease_active


# ---------------------------------------------------------------------------
# LifecyclePolicyService — high-level lifecycle and dependency policies
# ---------------------------------------------------------------------------


class LifecyclePolicyService:
    """
    Domain-level helper for task lifecycle and dependency policies.

    This service centralises rules that were previously embedded in
    application handlers so that the application layer can delegate
    the decision making to the domain.
    """

    @staticmethod
    def should_unblock_dependent(
        dependent: TaskAggregate,
        completed_task_ids: set[str],
    ) -> bool:
        """
        Return True if a dependent task should be considered unblocked
        after one of its dependencies has completed.

        A task is unblocked when:
          - it is in CREATED state, and
          - all entries in depends_on are in completed_task_ids.
        """
        if dependent.status != TaskStatus.CREATED:
            return False
        return dependent.is_unblocked(completed_task_ids)

    @staticmethod
    def should_requeue_after_failure(task: TaskAggregate) -> bool:
        """
        Return True if a FAILED task should be requeued according to its
        retry policy.
        """
        return task.status == TaskStatus.FAILED and task.can_retry()

    @staticmethod
    def should_cancel_after_failure(task: TaskAggregate) -> bool:
        """
        Return True if a FAILED task should be canceled because its retry
        policy is exhausted.
        """
        return task.status == TaskStatus.FAILED and not task.can_retry()