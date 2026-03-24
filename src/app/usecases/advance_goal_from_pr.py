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

from typing import Optional

import structlog

from src.domain import DomainEvent, EventPort
from src.domain.aggregates.goal import GoalStatus
from src.domain.aggregates.project_plan import ProjectPlanStatus
from src.domain.repositories.goal_repository import GoalRepositoryPort
from src.domain.repositories.project_plan_repository import ProjectPlanRepositoryPort

log = structlog.get_logger(__name__)

PRODUCER        = "goal-orchestrator"
MAX_CAS_RETRIES = 5
MAX_PLAN_CAS_RETRIES = 5


class AdvanceGoalFromPRUseCase:
    """
    Apply eligible PR-driven state transitions and emit the resulting events.

    When a goal transitions to MERGED, optionally triggers UnblockGoalsUseCase
    to start any dependent goals whose prerequisites are now all satisfied.
    """

    def __init__(
        self,
        goal_repo: GoalRepositoryPort,
        event_port: EventPort,
        unblock_goals_usecase=None,   # UnblockGoalsUseCase | None
        plan_repo: Optional[ProjectPlanRepositoryPort] = None,  # NEW — optional for backward compat
    ) -> None:
        self._goal_repo = goal_repo
        self._events    = event_port
        self._unblock   = unblock_goals_usecase
        self._plan_repo = plan_repo

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
                # When a goal reaches MERGED, release any dependent goals
                # whose prerequisites are now fully satisfied.
                if new_status == GoalStatus.MERGED and self._unblock:
                    self._unblock.execute(goal_id)
                    # Check if the active phase is now complete
                    self._check_phase_completion()
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
    # Phase completion trigger
    # ------------------------------------------------------------------

    def _check_phase_completion(self) -> None:
        """
        Check if the active phase is complete and trigger PHASE_REVIEW if so.

        Called when a goal reaches MERGED. If all goals in the active phase
        have reached MERGED, transition the project plan to PHASE_REVIEW.
        """
        if self._plan_repo is None:
            return  # Backward compatibility — no plan repo, nothing to check

        for attempt in range(MAX_PLAN_CAS_RETRIES):
            plan = self._plan_repo.get()
            if plan is None:
                return  # No plan yet

            if plan.status != ProjectPlanStatus.PHASE_ACTIVE:
                return  # Not in PHASE_ACTIVE mode

            active_phase = plan.current_phase()
            if active_phase is None:
                return  # No active phase

            phase_goal_names = set(active_phase.goal_names)
            if not phase_goal_names:
                return  # Guard: don't trigger on empty phase

            # Check if all goals in this phase have reached MERGED
            all_goals = self._goal_repo.list_all()
            phase_goals = [g for g in all_goals if g.name in phase_goal_names]
            all_merged = all(g.status == GoalStatus.MERGED for g in phase_goals)
            if not (all_merged and len(phase_goals) == len(phase_goal_names)):
                return

            expected_v = plan.state_version
            next_plan = plan.trigger_review()
            if self._plan_repo.update_if_version(expected_v, next_plan):
                log.info(
                    "project_plan.phase_review_triggered",
                    phase_index=next_plan.current_phase_index,
                )
                return

            log.warning("advance_pr.phase_review_cas_conflict", attempt=attempt)

        log.error("advance_pr.phase_review_cas_exhausted")

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
