"""
src/app/usecases/goal_finalize.py — Finalize a goal that reached APPROVED status.

Triggered by `orchestrate finalize <goal_id>` — a deliberate operator action.

In the PR-driven flow, "finalize" covers two scenarios:

  A. PR already merged on GitHub (goal status == MERGED):
     The reconciler's PR polling pass has already transitioned the goal to
     MERGED.  This use case is a no-op (idempotent) — it records the
     finalization and emits goal.finalized for audit purposes.

  B. Goal is APPROVED (CI green + approvals met) but PR not yet merged:
     The operator merges the PR manually through the GitHub UI or merge queue.
     This use case records the operator intent, but the actual merge must happen
     on GitHub.  The reconciler will pick up the merge and emit goal.merged.
     This use case DOES NOT bypass the GitHub PR — it never calls git merge
     directly. GitHub is the single source of truth for merge state.

Prerequisites (for scenario B):
  - GoalAggregate must be in APPROVED or MERGED status.
  - The PR must have been opened (pr_number must be set).

Deprecated direct-merge path:
  The original `goal_finalize.py` called git.merge_task_into_goal() directly
  (goal branch → main).  This path is removed in the PR-driven flow.
  All merges to main go through the GitHub PR gate.
"""
from __future__ import annotations

import structlog

from src.domain import DomainEvent, EventPort, GoalStatus
from src.domain.repositories.goal_repository import GoalRepositoryPort
from src.domain.value_objects.task import HistoryEntry
from datetime import datetime, timezone

log = structlog.get_logger(__name__)

PRODUCER = "goal-orchestrator"

_ELIGIBLE_STATUSES = {GoalStatus.APPROVED, GoalStatus.MERGED}


class GoalFinalizeUseCase:
    """
    Record finalization of a goal that has been approved or merged via GitHub PR.

    Does NOT perform any git operations — the GitHub PR merge is done by the
    operator (or merge queue) through the GitHub UI. This use case exists to:
      - Validate the goal is in an eligible state.
      - Emit goal.finalized for audit / dashboard consumption.
      - Record the finalization in the goal history.

    Raises ValueError if the goal is not in APPROVED or MERGED status.
    Raises KeyError if goal_id is not found.
    """

    def __init__(
        self,
        goal_repo: GoalRepositoryPort,
        event_port: EventPort,
    ) -> None:
        self._goal_repo = goal_repo
        self._events    = event_port

    def execute(self, goal_id: str) -> dict:
        """
        Finalize the goal. Returns a summary dict.
        Raises ValueError if goal is not APPROVED or MERGED.
        Raises KeyError if goal_id not found.
        """
        goal = self._goal_repo.load(goal_id)

        if goal.status not in _ELIGIBLE_STATUSES:
            raise ValueError(
                f"Goal '{goal_id}' is '{goal.status.value}'. "
                f"Only goals in {[s.value for s in _ELIGIBLE_STATUSES]} "
                "can be finalized. Wait for the PR to be approved or merged."
            )

        # Idempotency: reject repeated finalization
        already_finalized = any(h.event == "goal.finalized" for h in goal.history)
        if already_finalized:
            raise ValueError(
                f"Goal '{goal_id}' has already been finalized. "
                "Check the goal history for the original finalization record."
            )

        merged, total = goal.progress()
        log.info(
            "goal_finalize.recording",
            goal_id=goal_id,
            status=goal.status.value,
            pr_number=goal.pr_number,
            merged_tasks=merged,
            total_tasks=total,
        )

        # Record in goal history
        goal.history.append(HistoryEntry(
            event="goal.finalized",
            actor="operator",
            detail={
                "pr_number": goal.pr_number,
                "pr_status": goal.pr_status,
                "pr_head_sha": goal.pr_head_sha,
                "goal_status": goal.status.value,
            },
        ))
        goal.state_version += 1
        goal.updated_at = datetime.now(timezone.utc)
        self._goal_repo.save(goal)

        self._events.publish(DomainEvent(
            type="goal.finalized",
            producer=PRODUCER,
            payload={
                "goal_id":    goal_id,
                "branch":     goal.branch,
                "pr_number":  goal.pr_number,
                "goal_status": goal.status.value,
            },
        ))

        log.info(
            "goal_finalize.done",
            goal_id=goal_id,
            pr_number=goal.pr_number,
            status=goal.status.value,
        )

        return {
            "goal_id":    goal_id,
            "pr_number":  goal.pr_number,
            "pr_url":     goal.pr_html_url,
            "goal_status": goal.status.value,
        }
