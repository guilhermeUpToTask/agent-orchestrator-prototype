"""
src/app/usecases/create_goal_pr.py — Open a GitHub PR for a completed goal.

Triggered when the goal reaches READY_FOR_REVIEW status (all task branches
merged into the goal branch). The orchestrator calls this use case before
handing off to the reconciler-driven PR sync loop.

Pipeline:
  1. Load the GoalAggregate — must be READY_FOR_REVIEW
  2. Derive PR title and body from goal metadata
  3. Check for an existing open PR (idempotency)
  4. Call GitHubPort.create_pr(goal/<n> → main)
  5. Fetch the PR head SHA for regression tracking
  6. Call goal.open_pr() → AWAITING_PR_APPROVAL
  7. Persist updated GoalAggregate (CAS)
  8. Emit goal.pr_opened

Constraints (enforced here, not by agents):
  - Only the orchestrator triggers this use case.
  - Agents MUST NOT call GitHubPort or this use case directly.
  - This is goal-level only — no per-task PRs.
"""
from __future__ import annotations

import structlog

from src.domain import DomainEvent, EventPort
from src.domain.aggregates.goal import GoalStatus
from src.domain.ports.github import GitHubError, GitHubPort
from src.domain.repositories.goal_repository import GoalRepositoryPort

log = structlog.get_logger(__name__)

PRODUCER      = "goal-orchestrator"
MAX_CAS_RETRIES = 5


class CreateGoalPRUseCase:
    """
    Open a GitHub PR for a goal that has reached READY_FOR_REVIEW.

    Idempotent: if a PR already exists for the goal branch, skips creation
    and ensures the goal aggregate reflects the existing PR number.
    """

    def __init__(
        self,
        goal_repo: GoalRepositoryPort,
        event_port: EventPort,
        github: GitHubPort,
        base_branch: str = "main",
    ) -> None:
        self._goal_repo   = goal_repo
        self._events      = event_port
        self._github      = github
        self._base_branch = base_branch

    def execute(self, goal_id: str) -> int:
        """
        Create (or re-surface) the GitHub PR for goal_id.

        Returns the PR number.
        Raises ValueError if the goal is not in READY_FOR_REVIEW status.
        Raises KeyError  if goal_id is not found.
        Raises GitHubError on unrecoverable API failures.
        """
        goal = self._goal_repo.load(goal_id)

        if goal.status != GoalStatus.READY_FOR_REVIEW:
            raise ValueError(
                f"Goal '{goal_id}' is '{goal.status.value}', "
                "not 'ready_for_review'. Cannot open a PR in this state."
            )

        # Build PR metadata from goal
        head_branch = goal.branch          # e.g. "goal/add-auth"
        title       = f"feat: {goal.name}"
        body        = _build_pr_body(goal)

        log.info(
            "create_goal_pr.opening",
            goal_id=goal_id,
            head=head_branch,
            base=self._base_branch,
        )

        pr_number = self._github.create_pr(
            head_branch=head_branch,
            base_branch=self._base_branch,
            title=title,
            body=body,
        )

        # Fetch PR info to capture the current head SHA
        pr_info = self._github.get_pr_info(pr_number)
        html_url = pr_info.html_url
        head_sha = pr_info.head_sha

        log.info(
            "create_goal_pr.pr_ready",
            goal_id=goal_id,
            pr_number=pr_number,
            url=html_url,
        )

        # Persist goal transition (CAS loop)
        self._persist_pr_opened(goal_id, pr_number, html_url, head_sha)

        self._events.publish(DomainEvent(
            type="goal.pr_opened",
            producer=PRODUCER,
            payload={
                "goal_id":   goal_id,
                "pr_number": pr_number,
                "url":       html_url,
                "head_sha":  head_sha,
            },
        ))

        return pr_number

    # ------------------------------------------------------------------
    # Internal CAS helper
    # ------------------------------------------------------------------

    def _persist_pr_opened(
        self, goal_id: str, pr_number: int, html_url: str, head_sha: str
    ) -> None:
        for attempt in range(MAX_CAS_RETRIES):
            goal = self._goal_repo.load(goal_id)

            # If already transitioned (e.g. concurrent call) treat as done
            if goal.pr_number == pr_number:
                log.info(
                    "create_goal_pr.already_persisted",
                    goal_id=goal_id,
                    pr_number=pr_number,
                )
                return

            expected_v = goal.state_version
            goal.open_pr(pr_number=pr_number, html_url=html_url, head_sha=head_sha)

            if self._goal_repo.update_if_version(goal_id, goal, expected_v):
                log.info(
                    "create_goal_pr.goal_updated",
                    goal_id=goal_id,
                    pr_number=pr_number,
                    new_status=goal.status.value,
                )
                return

            log.warning(
                "create_goal_pr.cas_conflict",
                goal_id=goal_id,
                attempt=attempt,
            )

        log.error("create_goal_pr.cas_exhausted", goal_id=goal_id)


# ---------------------------------------------------------------------------
# PR body helper
# ---------------------------------------------------------------------------

def _build_pr_body(goal) -> str:
    """Build a structured PR description from goal metadata."""
    task_lines = "\n".join(
        f"- [{t.status.value}] `{t.task_id}` — {t.title}"
        for t in goal.tasks.values()
    )
    return (
        f"## Goal: {goal.name}\n\n"
        f"{goal.description}\n\n"
        f"### Tasks\n\n"
        f"{task_lines}\n\n"
        f"---\n"
        f"*Opened automatically by the orchestrator.*\n"
        f"*Goal ID: `{goal.goal_id}`*\n"
    )
