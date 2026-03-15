"""
src/app/handlers/task_manager.py — Task Manager: event-driven coordinator.

Architecture: the task manager is the single reactive coordinator for all
normal task flow.  It responds to events immediately and drives tasks
forward without any polling.

Event → Handler mapping:
  task.created   → handle_task_created   — select agent, assign, emit task.assigned
  task.requeued  → handle_task_requeued  — same as created (re-entry after failure)
  task.completed → handle_task_completed — unblock dependents, trigger auto-merge (future)
  task.failed    → handle_task_failed    — requeue (retries left) or cancel (exhausted)

The reconciler emits task.failed for dead-agent / lease-expired scenarios.
The task manager handles what to do with that failure — centralising
retry/cancel policy in one place.

Existing fixes retained from the code-review pass:
  #1.2  publish(task.assigned) after both CAS writes are confirmed durable
  #2.1  list_all() called once per handle_task_completed(), not per dependent
"""
from __future__ import annotations

import structlog

from src.core.models import Assignment, DomainEvent, TaskAggregate, TaskStatus
from src.core.ports import (
    AgentRegistryPort,
    EventPort,
    LeasePort,
    TaskRepositoryPort,
)
from src.core.services import LifecyclePolicyService, SchedulerService

log = structlog.get_logger(__name__)

TASK_MANAGER_AGENT_ID = "agent-task-manager"
MAX_RETRIES = 5


class TaskManagerHandler:
    """
    Event-driven coordinator for all task lifecycle transitions.

    Every public handle_* method corresponds to one event type consumed
    from the event stream.  Each method is idempotent: replaying the same
    event produces no side effects if the task has already advanced.
    """

    def __init__(
        self,
        task_repo: TaskRepositoryPort,
        agent_registry: AgentRegistryPort,
        event_port: EventPort,
        lease_port: LeasePort,
        scheduler: SchedulerService | None = None,
    ) -> None:
        self._repo = task_repo
        self._registry = agent_registry
        self._events = event_port
        self._lease = lease_port
        self._scheduler = scheduler or SchedulerService()
        self._lifecycle = LifecyclePolicyService()

    # ------------------------------------------------------------------
    # handle_task_created — assign a newly-created task
    # ------------------------------------------------------------------

    def handle_task_created(self, task_id: str) -> bool:
        """
        Attempt to assign task_id to the best eligible agent.
        Returns True on successful assignment, False if no eligible agent found.
        """
        log.info("task_manager.handling_created", task_id=task_id)
        return self._assign_with_retry(task_id)

    # ------------------------------------------------------------------
    # handle_task_requeued — re-assign after a failure
    # ------------------------------------------------------------------

    def handle_task_requeued(self, task_id: str) -> bool:
        """
        Re-attempt assignment for a task that has been requeued.
        Identical logic to handle_task_created — both start from an
        assignable state (CREATED or REQUEUED).
        """
        log.info("task_manager.handling_requeued", task_id=task_id)
        return self._assign_with_retry(task_id)

    # ------------------------------------------------------------------
    # handle_task_completed — unblock dependents
    # ------------------------------------------------------------------

    def handle_task_completed(self, completed_task_id: str) -> None:
        """
        Scan for dependent tasks that are now unblocked and assign them.

        A dependent task is dispatched only when ALL of its depends_on
        tasks have status == SUCCEEDED.

        list_all() is called once and the succeeded set is shared with
        every _try_assign call — eliminates O(N²) filesystem scans (#2.1).

        Future: this is also where auto-merge and goal-status updates will
        be triggered once those features are implemented.
        """
        log.info("task_manager.handling_completed", task_id=completed_task_id)

        all_tasks = self._repo.list_all()
        succeeded_ids = {t.task_id for t in all_tasks if t.status == TaskStatus.SUCCEEDED}

        for task in all_tasks:
            if completed_task_id not in task.depends_on:
                continue
            if not self._lifecycle.should_unblock_dependent(task, succeeded_ids):
                continue
            log.info(
                "task_manager.unblocking_dependent",
                task_id=task.task_id,
                completed=completed_task_id,
            )
            self._assign_with_retry(task.task_id, preloaded_succeeded=succeeded_ids)

    # ------------------------------------------------------------------
    # handle_task_failed — decide requeue or cancel
    # ------------------------------------------------------------------

    def handle_task_failed(self, task_id: str) -> None:
        """
        React to a task.failed event emitted by either:
          - the worker (agent exited non-zero, forbidden files, test failure), or
          - the reconciler (dead agent, expired lease).

        Policy:
          retry_policy.attempt < max_retries → requeue → emit task.requeued
          retry_policy.attempt >= max_retries → cancel  → emit task.canceled

        Requeuing emits task.requeued which this same handler will receive
        via handle_task_requeued, completing the retry loop event-driven.

        All state writes use optimistic CAS with retry.
        """
        log.info("task_manager.handling_failed", task_id=task_id)
        for attempt in range(MAX_RETRIES):
            try:
                self._try_handle_failed(task_id)
                return
            except _VersionConflict:
                log.warning(
                    "task_manager.version_conflict_on_failed",
                    task_id=task_id,
                    attempt=attempt,
                )
        log.error("task_manager.handle_failed_max_retries", task_id=task_id)

    def _try_handle_failed(self, task_id: str) -> None:
        try:
            task = self._repo.load(task_id)
        except KeyError:
            # Task YAML was deleted between sessions.  The task.failed event
            # was never ACKed (consumer group didn't exist yet) so Redis
            # replayed it on boot.  Nothing left to act on — discard.
            log.warning("task_manager.task_not_found_on_failed", task_id=task_id)
            return

        if task.status != TaskStatus.FAILED:
            # Stale event — task has already moved on (e.g. another consumer
            # handled it first, or the task was canceled externally).
            log.info(
                "task_manager.skip_not_failed",
                task_id=task_id,
                status=task.status.value,
            )
            return

        expected_v = task.state_version

        if self._lifecycle.should_requeue_after_failure(task):
            # Retries remain — requeue
            task.requeue()
            ok = self._repo.update_if_version(task_id, task, expected_v)
            if not ok:
                raise _VersionConflict()

            self._events.publish(DomainEvent(
                type="task.requeued",
                producer=TASK_MANAGER_AGENT_ID,
                payload={"task_id": task_id},
            ))
            log.info(
                "task_manager.requeued_after_failure",
                task_id=task_id,
                attempt=task.retry_policy.attempt,
                max_retries=task.retry_policy.max_retries,
            )
        else:
            # Retries exhausted — cancel permanently
            task.cancel("Max retries exhausted")
            ok = self._repo.update_if_version(task_id, task, expected_v)
            if not ok:
                raise _VersionConflict()

            self._events.publish(DomainEvent(
                type="task.canceled",
                producer=TASK_MANAGER_AGENT_ID,
                payload={
                    "task_id": task_id,
                    "reason": "max_retries_exhausted",
                },
            ))
            log.info(
                "task_manager.canceled_exhausted",
                task_id=task_id,
                attempts=task.retry_policy.attempt,
            )

    # ------------------------------------------------------------------
    # Assignment — shared by handle_task_created and handle_task_requeued
    # ------------------------------------------------------------------

    def _assign_with_retry(
        self,
        task_id: str,
        preloaded_succeeded: set[str] | None = None,
    ) -> bool:
        for attempt in range(MAX_RETRIES):
            try:
                return self._try_assign(task_id, preloaded_succeeded=preloaded_succeeded)
            except _VersionConflict:
                log.warning(
                    "task_manager.version_conflict",
                    task_id=task_id,
                    attempt=attempt,
                )
                # Discard pre-loaded state on conflict; reload fresh on next attempt
                preloaded_succeeded = None
        log.error("task_manager.assign_failed_max_retries", task_id=task_id)
        return False

    def _try_assign(
        self,
        task_id: str,
        preloaded_succeeded: set[str] | None = None,
    ) -> bool:
        try:
            task = self._repo.load(task_id)
        except KeyError:
            # Task was deleted between sessions; event replayed by id="0".
            log.warning("task_manager.task_not_found_on_assign", task_id=task_id)
            return False

        if task.status not in (TaskStatus.CREATED, TaskStatus.REQUEUED):
            log.info(
                "task_manager.skip_not_assignable",
                task_id=task_id,
                status=task.status.value,
            )
            return False

        # ----------------------------------------------------------------
        # Dependency check
        # ----------------------------------------------------------------
        if task.depends_on:
            if preloaded_succeeded is None:
                all_tasks = self._repo.list_all()
                preloaded_succeeded = {
                    t.task_id for t in all_tasks if t.status == TaskStatus.SUCCEEDED
                }
            if not task.is_unblocked(preloaded_succeeded):
                unmet = [dep for dep in task.depends_on if dep not in preloaded_succeeded]
                log.info(
                    "task_manager.skip_unmet_dependencies",
                    task_id=task_id,
                    unmet=unmet,
                )
                return False

        # ----------------------------------------------------------------
        # Agent selection
        # ----------------------------------------------------------------
        agents = self._registry.list_agents()
        agent = self._scheduler.select_agent(task, agents)
        if agent is None:
            log.warning("task_manager.no_eligible_agent", task_id=task_id)
            return False

        # ----------------------------------------------------------------
        # Write 1: persist assignment (persist-first rule)
        # ----------------------------------------------------------------
        expected_version = task.state_version
        assignment = Assignment(agent_id=agent.agent_id, lease_seconds=300)
        task.assign(assignment)
        if not self._repo.update_if_version(task_id, task, expected_version):
            raise _VersionConflict()

        # ----------------------------------------------------------------
        # Create lease
        # ----------------------------------------------------------------
        lease_token = self._lease.create_lease(
            task_id=task_id,
            agent_id=agent.agent_id,
            lease_seconds=assignment.lease_seconds,
        )
        task.assignment.lease_token = lease_token

        # ----------------------------------------------------------------
        # Write 2: persist lease token before waking the worker (#1.2)
        # If this write fails the worker never sees the event; revoke the
        # orphaned lease and let the outer retry loop try again cleanly.
        # ----------------------------------------------------------------
        token_version = task.state_version
        if not self._repo.update_if_version(task_id, task, token_version):
            self._lease.revoke_lease(lease_token)
            raise _VersionConflict()

        # ----------------------------------------------------------------
        # Publish only after both writes are durable (#1.2)
        # ----------------------------------------------------------------
        self._events.publish(DomainEvent(
            type="task.assigned",
            producer=TASK_MANAGER_AGENT_ID,
            correlation_id=task.feature_id,
            payload={
                "task_id": task_id,
                "agent_id": agent.agent_id,
                "project_id": task.feature_id,
            },
        ))
        log.info(
            "task_manager.assigned",
            task_id=task_id,
            agent_id=agent.agent_id,
            lease_token=lease_token,
        )
        return True


class _VersionConflict(Exception):
    pass