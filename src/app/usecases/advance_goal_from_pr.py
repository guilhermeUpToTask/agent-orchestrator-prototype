"""
src/app/usecases/advance_goal_from_pr.py — Drive GoalAggregate state transitions
from the latest PR observation.

Called by the Reconciler immediately after SyncGoalPRStatusUseCase on every
polling pass. This separation keeps sync (data fetch) and advance (state
machine step) in distinct, independently-testable units.

Transition table driven by goal.advance_from_pr_state():
  AWAITING_PR_APPROVAL + checks_passed + approved → APPROVED
  AWAITING_PR_APPROVAL + pr_status == "closed"   → FAILED
  APPROVED             + pr_status == "merged"   → MERGED
  APPROVED             + pr_status == "closed"   → FAILED

When the goal reaches APPROVED or MERGED, an appropriate domain event is
emitted so downstream consumers (sequential goal scheduler, dashboard) can
react immediately.

The TaskGraphOrchestrator reads goal.needs_next_goal_unlock() to decide
whether to release the next goal in a sequential plan. It gates on
APPROVED or MERGED to allow progression before the merge actually lands,
matching the spec requirement:
  "Only release next goals when goal.status in [APPROVED, MERGED]"
"""
from __future__ import annotations

import structlog

from src.domain import DomainEvent, EventPort
from src.domain.aggregates.goal import GoalStatus
from src.domain.repositories.goal_repository import GoalRepositoryPort

log = structlog.get_logger(__name__)

PRODUCER        = "goal-orchestrator"
MAX_CAS_RETRIES = 5


class AdvanceGoalFromPRUseCase:
    """
    Apply eligible PR-driven state transitions and emit the resulting events.
    """

    def __init__(
        self,
        goal_repo: GoalRepositoryPort,
        event_port: EventPort,
    ) -> None:
        self._goal_repo = goal_repo
        self._events    = event_port

    def execute(self, goal_id: str) -> GoalStatus | None:
        """
        Evaluate and apply any eligible transition for goal_id.

        Returns the new GoalStatus if a transition occurred, else None.
        """
        goal = self._goal_repo.get(goal_id)
        if goal is None:
            log.warning("advance_pr.goal_not_found", goal_id=goal_id)
            return None

        if goal.is_terminal():
            return None

        if goal.pr_number is None:
            return None

        # Record the status before we attempt to advance
        status_before = goal.status

        for attempt in range(MAX_CAS_RETRIES):
            goal = self._goal_repo.load(goal_id)
            if goal.is_terminal():
                return None

            expected_v = goal.state_version
            goal.advance_from_pr_state()

            new_status = goal.status
            if new_status == status_before:
                # No eligible transition — nothing to persist
                return None

            if self._goal_repo.update_if_version(goal_id, goal, expected_v):
                self._emit(goal_id, goal.pr_number, new_status, goal.pr_head_sha)
                log.info(
                    "advance_pr.transitioned",
                    goal_id=goal_id,
                    from_status=status_before.value,
                    to_status=new_status.value,
                )
                return new_status

            log.warning(
                "advance_pr.cas_conflict",
                goal_id=goal_id,
                attempt=attempt,
            )
            # Reload and retry — status_before already set for next iteration check
            goal = self._goal_repo.load(goal_id)
            status_before = goal.status  # re-anchor after reload

        log.error("advance_pr.cas_exhausted", goal_id=goal_id)
        return None

    # ------------------------------------------------------------------
    # Event emission
    # ------------------------------------------------------------------

    def _emit(
        self,
        goal_id: str,
        pr_number: int | None,
        new_status: GoalStatus,
        head_sha: str | None,
    ) -> None:
        payload_base = {
            "goal_id":   goal_id,
            "pr_number": pr_number,
            "head_sha":  head_sha,
        }

        if new_status == GoalStatus.APPROVED:
            self._events.publish(DomainEvent(
                type="goal.approved",
                producer=PRODUCER,
                payload=payload_base,
            ))

        elif new_status == GoalStatus.MERGED:
            self._events.publish(DomainEvent(
                type="goal.merged",
                producer=PRODUCER,
                payload=payload_base,
            ))

        elif new_status == GoalStatus.FAILED:
            self._events.publish(DomainEvent(
                type="goal.failed",
                producer=PRODUCER,
                payload={**payload_base, "reason": "PR closed without merging"},
            ))
