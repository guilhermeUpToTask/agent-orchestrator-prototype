"""
src/domain/ports/github.py — GitHub integration port.

Abstracts all GitHub API calls behind a clean domain contract.
Infrastructure adapters implement this; the domain and application layers
never import requests, httpx, or PyGithub directly.

Constraints enforced at this boundary:
  - Agents must NEVER call these methods directly.
  - Only orchestrator use cases (CreateGoalPRUseCase, SyncGoalPRStatusUseCase,
    AdvanceGoalFromPRUseCase) may invoke methods on this port.
  - GitHub is the single source of truth for merge state.
  - PRs are goal-level only — no per-task PRs.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.value_objects.pr import PRInfo


class GitHubPort(ABC):
    """
    Contract for creating and querying GitHub Pull Requests.

    All methods are goal-scoped. The orchestrator calls these on behalf of
    GoalAggregate lifecycle transitions; agents have no access to this port.
    """

    @abstractmethod
    def create_pr(
        self,
        *,
        head_branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> int:
        """
        Open a Pull Request from head_branch → base_branch.

        Returns the newly created PR number.
        Raises GitHubError if the PR cannot be created (e.g. already exists,
        branch not pushed, token lacking write:pull_request scope).
        Idempotency: callers should check for an existing PR before calling.
        """
        ...

    @abstractmethod
    def get_pr_info(self, pr_number: int) -> PRInfo:
        """
        Fetch the current observable state of a Pull Request.

        Returns a fully-populated PRInfo snapshot including:
          - PR status (open / closed / merged)
          - head SHA (used to detect new commits that reset CI state)
          - all check run results for the head commit
          - approval count and whether changes were requested

        Raises GitHubError if pr_number is not found or API call fails.
        """
        ...

    @abstractmethod
    def find_open_pr(self, head_branch: str, base_branch: str) -> int | None:
        """
        Return the PR number for an existing open PR from head → base,
        or None if no such PR exists.

        Used for idempotent PR creation: CreateGoalPRUseCase calls this
        first so re-running a finalized goal does not open a duplicate PR.
        """
        ...


class GitHubError(Exception):
    """Raised when a GitHub API call fails unrecoverably."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GitHubRateLimitError(GitHubError):
    """Raised when the GitHub API rate limit is exceeded."""
