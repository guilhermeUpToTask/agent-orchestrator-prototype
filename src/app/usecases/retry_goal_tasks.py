"""
src/app/usecases/retry_goal_tasks.py — Bulk retry of FAILED tasks.

Operator recovery action: requeue every FAILED task in a goal (or across all
goals) in one click. Delegates each requeue to TaskRetryUseCase so the
force-requeue invariant and the ``task.requeued`` event stay in one place.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from src.domain import GoalStatus, TaskStatus
from src.domain.repositories import TaskRepositoryPort
from src.domain.repositories.goal_repository import GoalRepositoryPort
from src.app.usecases.task_retry import TaskRetryUseCase

log = structlog.get_logger(__name__)

_ACTOR = "operator:retry-failed"

# Terminal-but-unsuccessful states an operator can retry. A task that hit a
# hard error and exhausted its retry budget ends up CANCELED (which also fails
# its goal); a transiently failed one is FAILED.
_RETRYABLE = {TaskStatus.FAILED, TaskStatus.CANCELED}


@dataclass
class RetryGoalTasksResult:
    requeued: list[str] = field(default_factory=list)
    goals_touched: list[str] = field(default_factory=list)


class RetryGoalTasksUseCase:
    def __init__(
        self,
        goal_repo: GoalRepositoryPort,
        task_repo: TaskRepositoryPort,
        task_retry: TaskRetryUseCase,
    ) -> None:
        self._goal_repo = goal_repo
        self._task_repo = task_repo
        self._task_retry = task_retry

    def retry_goal(self, goal_id: str) -> RetryGoalTasksResult:
        """Requeue every FAILED/CANCELED task of *goal_id* and reopen the goal.

        A permanently-canceled task fails its goal, so requeuing tasks is not
        enough — the goal is reopened (FAILED → RUNNING) so it can make progress
        again, and each requeued task's TaskSummary is synced to REQUEUED.
        """
        goal = self._goal_repo.get(goal_id)
        if goal is None:
            raise KeyError(goal_id)

        result = RetryGoalTasksResult()
        retryable = [
            task_id
            for task_id in goal.tasks
            if self._is_retryable(task_id)
        ]
        if not retryable:
            return result

        if goal.status == GoalStatus.FAILED:
            goal.reopen()  # un-terminal the goal so summaries/progress can update

        for task_id in retryable:
            self._task_retry.execute(task_id, actor=_ACTOR)  # task → REQUEUED + event
            goal.record_task_status(task_id, TaskStatus.REQUEUED)
            result.requeued.append(task_id)

        self._goal_repo.save(goal)
        result.goals_touched.append(goal_id)
        log.info("retry_goal_tasks.goal", goal_id=goal_id, requeued=result.requeued)
        return result

    def _is_retryable(self, task_id: str) -> bool:
        try:
            return self._task_repo.load(task_id).status in _RETRYABLE
        except KeyError:
            return False  # summary stub without a created task yet

    def retry_all(self) -> RetryGoalTasksResult:
        """Requeue every FAILED task across all goals."""
        result = RetryGoalTasksResult()
        for goal in self._goal_repo.list_all():
            goal_result = self.retry_goal(goal.goal_id)
            result.requeued.extend(goal_result.requeued)
            result.goals_touched.extend(goal_result.goals_touched)
        log.info("retry_goal_tasks.all", requeued=result.requeued)
        return result
