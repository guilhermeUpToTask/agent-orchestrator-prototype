"""
src/app/usecases/goal_finalize.py — Finalize a completed goal.

Triggered by `orchestrate finalize <goal_id>` — a deliberate operator action.

Prerequisites:
  - GoalAggregate must be in COMPLETED status
  - goal/<n> branch must exist and be up to date

Pipeline:
  1. Load and validate goal (must be COMPLETED)
  2. Merge goal/<n> → main on the target repo
  3. Emit goal.finalized

The merge to main is the only step that touches main. The goal branch itself
is preserved after finalization for audit purposes.
"""
from __future__ import annotations

import structlog

from src.domain import DomainEvent, EventPort, GitWorkspacePort, GoalStatus
from src.domain.repositories.goal_repository import GoalRepositoryPort

log = structlog.get_logger(__name__)

PRODUCER = "goal-orchestrator"


class GoalFinalizeUseCase:
    """
    Merge the completed goal branch into main.

    Raises ValueError if the goal is not in COMPLETED status.
    """

    def __init__(
        self,
        goal_repo: GoalRepositoryPort,
        event_port: EventPort,
        git_workspace: GitWorkspacePort,
        repo_url: str,
    ) -> None:
        self._goal_repo  = goal_repo
        self._events     = event_port
        self._git        = git_workspace
        self._repo_url   = repo_url

    def execute(self, goal_id: str) -> str:
        """
        Finalize the goal. Returns the merge commit sha.
        Raises ValueError if goal is not COMPLETED.
        Raises KeyError if goal_id not found.
        """
        goal = self._goal_repo.load(goal_id)

        if goal.status != GoalStatus.COMPLETED:
            raise ValueError(
                f"Goal '{goal_id}' is {goal.status.value}, not completed. "
                "Only completed goals can be finalized."
            )

        # Detect repeat finalization by checking if a goal.finalized event
        # was already emitted (tracked via a simple flag in the history).
        already_finalized = any(h.event == "goal.finalized" for h in goal.history)
        if already_finalized:
            raise ValueError(
                f"Goal '{goal_id}' has already been finalized. "
                "Check the goal history for the original commit sha."
            )

        merged, total = goal.progress()
        log.info(
            "goal_finalize.merging",
            goal_id=goal_id,
            branch=goal.branch,
            merged_tasks=merged,
            total_tasks=total,
        )

        commit_sha = self._git.merge_task_into_goal(
            repo_url=self._repo_url,
            task_branch=goal.branch,
            goal_branch="main",
            commit_message=f"feat: merge goal/{goal.name} into main",
        )

        self._events.publish(DomainEvent(
            type="goal.finalized",
            producer=PRODUCER,
            payload={
                "goal_id":    goal_id,
                "branch":     goal.branch,
                "commit_sha": commit_sha,
            },
        ))

        # Record finalization in the goal history so repeat calls are rejected.
        from src.domain.value_objects.task import HistoryEntry
        from datetime import datetime, timezone
        goal.history.append(HistoryEntry(
            event="goal.finalized",
            actor="operator",
            detail={"commit_sha": commit_sha},
        ))
        self._goal_repo.save(goal)

        log.info(
            "goal_finalize.done",
            goal_id=goal_id,
            commit_sha=commit_sha,
        )
        return commit_sha
