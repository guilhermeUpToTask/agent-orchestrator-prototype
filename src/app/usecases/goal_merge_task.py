"""
src/app/usecases/goal_merge_task.py — Merge a completed task into the goal branch.

Called by the TaskGraphOrchestrator whenever it receives a task.completed event.

Pipeline:
  1. Load the task — extract goal_id and task branch from constraints
  2. Skip tasks not associated with a goal (feature_id is None or unknown goal)
  3. Merge task branch → goal branch on the target repo
  4. Transition TaskAggregate → MERGED (CAS with retry)
  5. Update GoalAggregate via record_task_merged() (CAS with retry)
  6. If goal is now COMPLETED, emit goal.completed

CAS retries follow the same pattern as TaskAssignUseCase: reload on version
conflict, up to MAX_CAS_RETRIES attempts. A persistent conflict after all
retries is logged as an error but does not raise — the reconciler will
eventually detect the inconsistency.
"""
from __future__ import annotations

import structlog

from src.app.telemetry.service import TelemetryService
from src.domain import (
    DomainEvent,
    EventPort,
    GitWorkspacePort,
    GoalStatus,
    TelemetryEmitterPort,
    TaskStatus,
)
from src.domain.repositories import TaskRepositoryPort
from src.domain.repositories.goal_repository import GoalRepositoryPort

log = structlog.get_logger(__name__)

PRODUCER = "goal-orchestrator"
MAX_CAS_RETRIES = 5


class GoalMergeTaskUseCase:
    """
    Merge a succeeded task's branch into the goal branch and update both aggregates.
    """

    def __init__(
        self,
        task_repo: TaskRepositoryPort,
        goal_repo: GoalRepositoryPort,
        event_port: EventPort,
        git_workspace: GitWorkspacePort,
        repo_url: str,
        telemetry_emitter: TelemetryEmitterPort | None = None,
    ) -> None:
        self._task_repo  = task_repo
        self._goal_repo  = goal_repo
        self._events     = event_port
        self._git        = git_workspace
        self._repo_url   = repo_url
        self._telemetry  = TelemetryService(telemetry_emitter, producer=PRODUCER)

    def execute(self, task_id: str) -> None:
        """Handle a task.completed event for task_id."""
        # ------------------------------------------------------------------
        # 1. Load task
        # ------------------------------------------------------------------
        try:
            task = self._task_repo.load(task_id)
        except KeyError:
            log.warning("goal_merge.task_not_found", task_id=task_id)
            return

        goal_id = task.feature_id
        if not goal_id:
            log.debug("goal_merge.task_has_no_goal", task_id=task_id)
            return

        # ------------------------------------------------------------------
        # 2. Load goal
        # ------------------------------------------------------------------
        goal = self._goal_repo.get(goal_id)
        if goal is None:
            log.warning(
                "goal_merge.goal_not_found",
                task_id=task_id,
                goal_id=goal_id,
            )
            return

        if goal.is_terminal():
            log.info(
                "goal_merge.goal_already_terminal",
                goal_id=goal_id,
                status=goal.status.value,
            )
            return

        if task.status == TaskStatus.MERGED:
            log.info("goal_merge.task_already_merged", task_id=task_id)
            self._update_goal(goal_id, task_id)
            return

        if task.status != TaskStatus.SUCCEEDED:
            log.warning(
                "goal_merge.task_not_mergeable",
                task_id=task_id,
                status=task.status.value,
            )
            return

        # ------------------------------------------------------------------
        # 3. Merge task branch into goal branch
        # ------------------------------------------------------------------
        constraints  = task.execution.constraints
        task_branch  = constraints.get("task_branch", f"task/{task_id}")
        goal_branch  = constraints.get("goal_branch", goal.branch)
        commit_msg   = f"merge: task/{task_id} into {goal_branch}"

        log.info(
            "goal_merge.merging",
            task_id=task_id,
            task_branch=task_branch,
            goal_branch=goal_branch,
        )
        merge_sha = self._git.merge_task_into_goal(
            repo_url=self._repo_url,
            task_branch=task_branch,
            goal_branch=goal_branch,
            commit_message=commit_msg,
        )
        log.info("goal_merge.merged", task_id=task_id, sha=merge_sha)

        # ------------------------------------------------------------------
        # 4. Transition TaskAggregate → MERGED (CAS)
        # ------------------------------------------------------------------
        if not self._mark_task_merged(task_id):
            return

        # ------------------------------------------------------------------
        # 5 & 6. Update GoalAggregate (CAS) + emit goal.completed if done
        # ------------------------------------------------------------------
        self._update_goal(goal_id, task_id)

    # ------------------------------------------------------------------
    # Internal: CAS helpers
    # ------------------------------------------------------------------

    def _mark_task_merged(self, task_id: str) -> bool:
        for attempt in range(MAX_CAS_RETRIES):
            task = self._task_repo.load(task_id)
            if task.status == TaskStatus.MERGED:
                return True  # already done — idempotent
            if task.status != TaskStatus.SUCCEEDED:
                log.warning(
                    "goal_merge.task_not_succeeded_skipping",
                    task_id=task_id,
                    status=task.status.value,
                )
                return False
            expected_v = task.state_version
            task.mark_merged()
            if self._task_repo.update_if_version(task_id, task, expected_v):
                return True
            log.warning(
                "goal_merge.task_cas_conflict",
                task_id=task_id,
                attempt=attempt,
            )
        log.error("goal_merge.task_cas_exhausted", task_id=task_id)
        return False

    def _update_goal(self, goal_id: str, task_id: str) -> None:
        trace = self._telemetry.start_trace(goal_id=goal_id, correlation_id=goal_id)
        for attempt in range(MAX_CAS_RETRIES):
            goal = self._goal_repo.load(goal_id)
            if goal.is_terminal():
                return
            expected_v = goal.state_version
            goal.record_task_merged(task_id)
            if self._goal_repo.update_if_version(goal_id, goal, expected_v):
                if goal.status == GoalStatus.READY_FOR_REVIEW:
                    merged, total = goal.progress()
                    self._telemetry.emit(
                        "goal.completed",
                        self._telemetry.start_span(trace),
                        payload={"goal_id": goal_id, "merged": merged, "total": total},
                        goal_id=goal_id,
                        task_id=task_id,
                    )
                    self._events.publish(DomainEvent(
                        type="goal.ready_for_review",
                        producer=PRODUCER,
                        payload={
                            "goal_id": goal_id,
                            "branch":  goal.branch,
                            "merged":  merged,
                            "total":   total,
                        },
                    ))
                    log.info(
                        "goal_merge.goal_ready_for_review",
                        goal_id=goal_id,
                        merged=merged,
                        total=total,
                    )
                return
            log.warning(
                "goal_merge.goal_cas_conflict",
                goal_id=goal_id,
                task_id=task_id,
                attempt=attempt,
            )
        log.error("goal_merge.goal_cas_exhausted", goal_id=goal_id, task_id=task_id)
