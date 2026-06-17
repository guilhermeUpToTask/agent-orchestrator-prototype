"""
src/app/reconciliation/goal_pr_reconciler.py — GitHub PR polling control loop.

For every goal in a PR-phase status (AWAITING_PR_APPROVAL or APPROVED), polls
GitHub to sync PR state and drives eligible state-machine transitions. Each goal
is delegated to SyncGoalPRStatusUseCase + AdvanceGoalFromPRUseCase; a single
failing API call is isolated per-goal and never blocks the pass.
"""
from __future__ import annotations

from typing import Any

import structlog

from src.app.reconciliation.control_loop import ControlLoop
from src.domain.aggregates.goal import GoalStatus
from src.domain.repositories.goal_repository import GoalRepositoryPort

log = structlog.get_logger(__name__)

_PR_PHASE_STATUSES = {
    GoalStatus.AWAITING_PR_APPROVAL,
    GoalStatus.APPROVED,
}


class GoalPRReconciler(ControlLoop):
    """Polls GitHub for goals awaiting/approved on a PR and advances them."""

    name = "goal_pr"

    def __init__(
        self,
        goal_repo: GoalRepositoryPort,
        sync_pr_usecase: Any,
        advance_pr_usecase: Any,
        interval_seconds: int = 60,
    ) -> None:
        self._goal_repo = goal_repo
        self._sync_pr = sync_pr_usecase
        self._advance_pr = advance_pr_usecase
        self.interval_seconds = interval_seconds

    def reconcile_once(self) -> None:
        try:
            goals = self._goal_repo.list_all()
        except Exception as exc:
            log.error("reconciler.pr_poll.list_goals_failed", error=str(exc))
            return

        pr_phase_goals = [g for g in goals if g.status in _PR_PHASE_STATUSES]
        log.info("reconciler.pr_poll.pass", pr_phase_goals=len(pr_phase_goals))

        for goal in pr_phase_goals:
            try:
                self._poll_goal_pr(goal.goal_id)
            except Exception as exc:
                log.exception(
                    "reconciler.pr_poll.goal_error",
                    goal_id=goal.goal_id,
                    error=str(exc),
                )

    def _poll_goal_pr(self, goal_id: str) -> None:
        """Sync + advance a single goal's PR state."""
        log.debug("reconciler.pr_poll.goal", goal_id=goal_id)
        self._sync_pr.execute(goal_id)
        self._advance_pr.execute(goal_id)
