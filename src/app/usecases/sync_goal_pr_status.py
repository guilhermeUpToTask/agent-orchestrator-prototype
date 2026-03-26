"""
src/app/usecases/sync_goal_pr_status.py — Synchronise GoalAggregate with GitHub PR state.

Called by the Reconciler on every polling pass for goals in a PR-phase status
(AWAITING_PR_APPROVAL or APPROVED). This is the only place where GitHub's
view of the PR flows into the GoalAggregate.

Pipeline:
  1. Load the GoalAggregate — skip if not in a PR-phase status
  2. Fetch PRInfo from GitHubPort
  3. Evaluate CI gates against ProjectSpec.ci configuration
  4. Call goal.sync_pr_state() with current observations
  5. Persist updated state (CAS)
  6. Emit goal.pr_state_synced

This use case does NOT drive state transitions (AWAITING → APPROVED etc.).
That is the responsibility of AdvanceGoalFromPRUseCase, which is called
immediately after this one by the reconciler.
"""
from __future__ import annotations

import structlog

from src.domain import DomainEvent, EventPort
from src.domain.aggregates.goal import GoalStatus
from src.domain.ports.github import GitHubError, GitHubPort
from src.domain.project_spec.aggregate import ProjectSpec
from src.domain.repositories.goal_repository import GoalRepositoryPort

log = structlog.get_logger(__name__)

PRODUCER        = "reconciler"
MAX_CAS_RETRIES = 5

_PR_PHASE_STATUSES = {
    GoalStatus.AWAITING_PR_APPROVAL,
    GoalStatus.APPROVED,
}


class SyncGoalPRStatusUseCase:
    """
    Poll GitHub for the current PR state and update the GoalAggregate.

    spec is optional: when None, the CI gate is treated as unconfigured
    (no required checks, min_approvals=0). Inject the real ProjectSpec
    for production polling so the gate defined in project_spec.yaml is
    enforced.
    """

    def __init__(
        self,
        goal_repo: GoalRepositoryPort,
        event_port: EventPort,
        github: GitHubPort,
        spec: ProjectSpec | None = None,
    ) -> None:
        self._goal_repo = goal_repo
        self._events    = event_port
        self._github    = github
        self._spec      = spec

    def execute(self, goal_id: str) -> bool:
        """
        Sync the PR state for goal_id.

        Returns True if the goal was updated, False if skipped (wrong status,
        no PR number, or GitHub error that was safely swallowed).

        This method never raises on transient GitHub errors — it logs the
        error and returns False so the reconciler loop continues normally.
        """
        goal = self._goal_repo.get(goal_id)
        if goal is None:
            log.warning("sync_pr.goal_not_found", goal_id=goal_id)
            return False

        if goal.status not in _PR_PHASE_STATUSES:
            log.debug("sync_pr.skipped_wrong_status",
                      goal_id=goal_id, status=goal.status.value)
            return False

        if goal.pr_number is None:
            log.warning("sync_pr.no_pr_number", goal_id=goal_id)
            return False

        # ------------------------------------------------------------------
        # Fetch current PR state from GitHub
        # ------------------------------------------------------------------
        try:
            pr_info = self._github.get_pr_info(goal.pr_number)
        except GitHubError as exc:
            log.error(
                "sync_pr.github_error",
                goal_id=goal_id,
                pr_number=goal.pr_number,
                error=str(exc),
            )
            return False

        # ------------------------------------------------------------------
        # Evaluate CI gate from ProjectSpec
        # ------------------------------------------------------------------
        required_checks = list(self._spec.ci.required_checks) if self._spec else []
        min_approvals   = self._spec.ci.min_approvals if self._spec else 0

        checks_passed = pr_info.all_required_checks_passed(required_checks)
        approved      = pr_info.meets_approval_gate(min_approvals)

        log.info(
            "sync_pr.polled",
            goal_id=goal_id,
            pr_number=goal.pr_number,
            pr_status=pr_info.status.value,
            checks_passed=checks_passed,
            approved=approved,
            approval_count=pr_info.approval_count,
            head_sha=pr_info.head_sha[:8] if pr_info.head_sha else "?",
        )

        # ------------------------------------------------------------------
        # Persist via CAS
        # ------------------------------------------------------------------
        return self._persist_sync(
            goal_id=goal_id,
            pr_status=pr_info.status.value,
            checks_passed=checks_passed,
            approved=approved,
            head_sha=pr_info.head_sha,
            approval_count=pr_info.approval_count,
        )

    # ------------------------------------------------------------------
    # Internal CAS helper
    # ------------------------------------------------------------------

    def _persist_sync(
        self,
        *,
        goal_id: str,
        pr_status: str,
        checks_passed: bool,
        approved: bool,
        head_sha: str,
        approval_count: int,
    ) -> bool:
        for attempt in range(MAX_CAS_RETRIES):
            goal = self._goal_repo.load(goal_id)

            if goal.status not in _PR_PHASE_STATUSES:
                log.info(
                    "sync_pr.status_changed_during_cas",
                    goal_id=goal_id,
                    status=goal.status.value,
                )
                return False

            expected_v = goal.state_version
            goal.sync_pr_state(
                pr_status=pr_status,
                checks_passed=checks_passed,
                approved=approved,
                head_sha=head_sha,
                approval_count=approval_count,
            )

            if self._goal_repo.update_if_version(goal_id, goal, expected_v):
                self._events.publish(DomainEvent(
                    type="goal.pr_state_synced",
                    producer=PRODUCER,
                    payload={
                        "goal_id":        goal_id,
                        "pr_number":      goal.pr_number,
                        "pr_status":      pr_status,
                        "checks_passed":  checks_passed,
                        "approved":       approved,
                        "approval_count": approval_count,
                    },
                ))
                return True

            log.warning(
                "sync_pr.cas_conflict",
                goal_id=goal_id,
                attempt=attempt,
            )

        log.error("sync_pr.cas_exhausted", goal_id=goal_id)
        return False
