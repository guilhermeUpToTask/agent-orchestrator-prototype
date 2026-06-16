"""
src/app/usecases/task_assign.py — Task assignment use case.

Encapsulates the full assignment pipeline:
  1. Load task, verify it is in an assignable state
  2. Verify all dependencies have succeeded
  3. Select the best eligible agent via SchedulerService
  4. CAS write 1 — persist assignment (persist-first rule)
  5. Create lease
  6. CAS write 2 — persist lease token before waking the worker
  7. Publish task.assigned event (only after both writes are durable)

Optimistic concurrency: a _VersionConflict raised inside _attempt()
bubbles up to execute(), which retries up to MAX_CAS_RETRIES times,
reloading fresh state on each attempt.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

import structlog

from src.domain import (
    Assignment,
    DomainEvent,
    SchedulerService,
    TaskAggregate,
    TaskStatus,
)
from src.domain.ports import EventPort, LeasePort
from src.domain.repositories import AgentRegistryPort, TaskRepositoryPort

log = structlog.get_logger(__name__)

PRODUCER = "agent-task-manager"
MAX_CAS_RETRIES = 5


class AssignOutcome(str, Enum):
    ASSIGNED          = "assigned"
    NOT_ASSIGNABLE    = "not_assignable"    # wrong status
    DEPS_NOT_MET      = "deps_not_met"      # dependencies not yet succeeded
    NO_ELIGIBLE_AGENT = "no_eligible_agent" # scheduler found no match
    AT_CAPACITY       = "at_capacity"       # capable agent(s) exist but all are full
    NOT_FOUND         = "not_found"         # task deleted between events


@dataclass(frozen=True)
class TaskAssignResult:
    outcome: AssignOutcome
    task_id: str
    agent_id: Optional[str] = None          # set only on ASSIGNED


class _VersionConflict(Exception):
    pass


class TaskAssignUseCase:
    """
    Assign a single task to the best eligible agent.

    Shared by handle_task_created and handle_task_requeued — both start
    from an assignable state (CREATED or REQUEUED) and follow the same
    pipeline.

    preloaded_succeeded may be passed by TaskUnblockUseCase to avoid an
    O(N²) list_all() scan when unblocking multiple dependents at once.
    """

    def __init__(
        self,
        task_repo: TaskRepositoryPort,
        agent_registry: AgentRegistryPort,
        event_port: EventPort,
        lease_port: LeasePort,
        scheduler: SchedulerService | None = None,
    ) -> None:
        self._repo      = task_repo
        self._registry  = agent_registry
        self._events    = event_port
        self._lease     = lease_port
        self._scheduler = scheduler or SchedulerService()

    def execute(
        self,
        task_id: str,
        preloaded_succeeded: set[str] | None = None,
    ) -> TaskAssignResult:
        for attempt in range(MAX_CAS_RETRIES):
            try:
                return self._attempt(task_id, preloaded_succeeded)
            except _VersionConflict:
                log.warning(
                    "task_assign.version_conflict",
                    task_id=task_id,
                    attempt=attempt,
                )
                preloaded_succeeded = None   # stale — reload on next try

        log.error("task_assign.max_retries_exhausted", task_id=task_id)
        return TaskAssignResult(outcome=AssignOutcome.NOT_ASSIGNABLE, task_id=task_id)

    # ------------------------------------------------------------------
    # Single attempt
    # ------------------------------------------------------------------

    @staticmethod
    def _in_flight_by_agent(all_tasks: list[TaskAggregate]) -> dict[str, int]:
        """Count non-terminal in-flight tasks (ASSIGNED/IN_PROGRESS) per agent."""
        counts: dict[str, int] = {}
        for t in all_tasks:
            if t.status in (TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS) and t.assignment:
                counts[t.assignment.agent_id] = counts.get(t.assignment.agent_id, 0) + 1
        return counts

    def _attempt(
        self,
        task_id: str,
        preloaded_succeeded: set[str] | None,
    ) -> TaskAssignResult:
        # Load
        try:
            task = self._repo.load(task_id)
        except KeyError:
            log.warning("task_assign.task_not_found", task_id=task_id)
            return TaskAssignResult(outcome=AssignOutcome.NOT_FOUND, task_id=task_id)

        # Assignability guard
        if not task.is_assignable():
            log.info(
                "task_assign.skip_not_assignable",
                task_id=task_id,
                status=task.status.value,
            )
            return TaskAssignResult(outcome=AssignOutcome.NOT_ASSIGNABLE, task_id=task_id)

        # A single repo scan serves both the dependency check and the capacity
        # gate; reused so we never list_all twice in one attempt.
        all_tasks: list[TaskAggregate] | None = None

        # Dependency check
        if task.depends_on:
            if preloaded_succeeded is None:
                all_tasks = self._repo.list_all()
                preloaded_succeeded = {
                    t.task_id for t in all_tasks
                    if t.status == TaskStatus.SUCCEEDED
                }
            if not task.is_unblocked(preloaded_succeeded):
                unmet = [d for d in task.depends_on if d not in preloaded_succeeded]
                log.info(
                    "task_assign.deps_not_met",
                    task_id=task_id,
                    unmet=unmet,
                )
                return TaskAssignResult(outcome=AssignOutcome.DEPS_NOT_MET, task_id=task_id)

        # Agent selection
        agents = self._registry.list_agents()
        eligible = self._scheduler.eligible_agents(task, agents)
        if not eligible:
            required = task.agent_selector.required_capability
            reason = f"No active agent with capability '{required}'"
            # Persist + publish only on transition into the flagged state — this
            # runs every reconciler pass, so we must not re-emit each time.
            if task.unassignable_reason != reason:
                expected_v = task.state_version
                task.mark_unassignable(reason)
                if not self._repo.update_if_version(task_id, task, expected_v):
                    raise _VersionConflict()
                self._events.publish(DomainEvent(
                    type="task.unassignable",
                    producer=PRODUCER,
                    correlation_id=task.feature_id,
                    payload={
                        "task_id": task_id,
                        "required_capability": required,
                        "reason": reason,
                    },
                ))
            log.warning("task_assign.no_eligible_agent", task_id=task_id, required_capability=required)
            return TaskAssignResult(outcome=AssignOutcome.NO_ELIGIBLE_AGENT, task_id=task_id)

        # Capacity gate: a capable agent exists, but skip any that are already
        # running their max_concurrent_tasks. Tasks then stay CREATED (queued)
        # until a worker frees up, instead of being assigned in bulk to one
        # worker that can only run them serially.
        if all_tasks is None:
            all_tasks = self._repo.list_all()
        in_flight = self._in_flight_by_agent(all_tasks)
        free = [a for a in eligible if in_flight.get(a.agent_id, 0) < a.max_concurrent_tasks]
        if not free:
            log.info("task_assign.at_capacity", task_id=task_id)
            return TaskAssignResult(outcome=AssignOutcome.AT_CAPACITY, task_id=task_id)

        agent = self._scheduler.select_agent(task, free)
        if agent is None:  # defensive — free is non-empty and capability-matched
            return TaskAssignResult(outcome=AssignOutcome.AT_CAPACITY, task_id=task_id)

        # Write 1 — persist assignment before touching Redis
        expected_v = task.state_version
        assignment = Assignment(agent_id=agent.agent_id, lease_seconds=300)
        task.assign(assignment)
        if not self._repo.update_if_version(task_id, task, expected_v):
            raise _VersionConflict()

        # Create lease
        lease_token = self._lease.create_lease(
            task_id=task_id,
            agent_id=agent.agent_id,
            lease_seconds=assignment.lease_seconds,
        )
        assert task.assignment is not None  # set by the write above
        task.assignment.lease_token = lease_token

        # Write 2 — persist lease token before waking the worker
        # If this write fails, revoke the orphaned lease and retry cleanly.
        token_v = task.state_version
        if not self._repo.update_if_version(task_id, task, token_v):
            self._lease.revoke_lease(lease_token)
            raise _VersionConflict()

        # Publish only after both writes are durable
        self._events.publish(DomainEvent(
            type="task.assigned",
            producer=PRODUCER,
            correlation_id=task.feature_id,
            payload={
                "task_id":    task_id,
                "agent_id":   agent.agent_id,
                "project_id": task.feature_id,
            },
        ))
        log.info(
            "task_assign.assigned",
            task_id=task_id,
            agent_id=agent.agent_id,
        )
        return TaskAssignResult(
            outcome=AssignOutcome.ASSIGNED,
            task_id=task_id,
            agent_id=agent.agent_id,
        )
