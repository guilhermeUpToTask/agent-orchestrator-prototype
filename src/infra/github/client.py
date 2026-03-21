"""
src/infra/github/client.py — GitHubPort adapter using the GitHub REST API.

Uses only the standard-library `urllib` + `json` to avoid adding a heavy
dependency (PyGithub / httpx) that is not already in the project.

Authentication:
  Pass a Personal Access Token (PAT) with scopes:
    repo  (read/write PRs, check runs, reviews)
  Set GITHUB_TOKEN in the environment; the factory reads it automatically.

Rate limits:
  Primary rate limit: 5 000 req/hour for authenticated users.
  The client raises GitHubRateLimitError on HTTP 429 / X-RateLimit-Remaining==0
  so the reconciler can back off and retry.

Single-writer invariant:
  This client never merges PRs.  Merge is done by a human reviewer (or a
  protected-branch merge queue) through the GitHub UI — not by the orchestrator.
  The orchestrator only READS PR state and CREATES PRs.

Thread safety:
  The adapter is stateless. It is safe to share across threads, but each
  thread should use its own requests-level session (urllib connections are
  not shared here).
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import structlog

from src.domain.ports.github import GitHubError, GitHubPort, GitHubRateLimitError
from src.domain.value_objects.pr import (
    CheckRunResult,
    PRCheckConclusion,
    PRInfo,
    PRStatus,
)

log = structlog.get_logger(__name__)

_GITHUB_API = "https://api.github.com"
_ACCEPT     = "application/vnd.github+json"
_API_VER    = "2022-11-28"


class GitHubClient(GitHubPort):
    """
    Adapter that talks to GitHub's REST v3 API.

    Parameters
    ----------
    token       : GitHub PAT (repo scope required)
    owner       : repository owner (user or org slug)
    repo        : repository name
    timeout_s   : per-request TCP timeout in seconds (default 15)
    """

    def __init__(
        self,
        token: str,
        owner: str,
        repo: str,
        timeout_s: int = 15,
    ) -> None:
        if not token:
            raise ValueError(
                "GitHubClient requires a non-empty GitHub token. "
                "Set the GITHUB_TOKEN environment variable."
            )
        self._token   = token
        self._owner   = owner
        self._repo    = repo
        self._timeout = timeout_s
        self._base    = f"{_GITHUB_API}/repos/{owner}/{repo}"

    # ------------------------------------------------------------------
    # GitHubPort
    # ------------------------------------------------------------------

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
        Returns the PR number.
        Raises GitHubError on API failure.
        """
        payload = {
            "title": title,
            "head":  head_branch,
            "base":  base_branch,
            "body":  body,
            "draft": False,
        }
        log.info(
            "github.create_pr",
            head=head_branch,
            base=base_branch,
            title=title,
        )
        data = self._post("/pulls", payload)
        pr_number = data["number"]
        log.info("github.pr_created", pr_number=pr_number, url=data.get("html_url"))
        return pr_number

    def get_pr_info(self, pr_number: int) -> PRInfo:
        """
        Return a full PRInfo snapshot for the given PR number.
        Fetches PR details, check runs, and reviews in separate API calls.
        """
        log.debug("github.get_pr_info", pr_number=pr_number)

        # 1. PR details
        pr_data   = self._get(f"/pulls/{pr_number}")
        head_sha  = pr_data["head"]["sha"]
        html_url  = pr_data["html_url"]
        title     = pr_data["title"]
        head_ref  = pr_data["head"]["ref"]
        base_ref  = pr_data["base"]["ref"]
        pr_state  = pr_data["state"]          # "open" | "closed"
        merged    = pr_data.get("merged", False)

        if merged:
            pr_status = PRStatus.MERGED
        elif pr_state == "closed":
            pr_status = PRStatus.CLOSED
        else:
            pr_status = PRStatus.OPEN

        # 2. Check runs (via Commit Statuses combined API — returns all runs)
        check_runs = self._fetch_check_runs(head_sha)

        # 3. Reviews
        approval_count, changes_requested = self._fetch_review_summary(pr_number)

        return PRInfo(
            pr_number=pr_number,
            status=pr_status,
            head_branch=head_ref,
            base_branch=base_ref,
            head_sha=head_sha,
            html_url=html_url,
            title=title,
            check_runs=check_runs,
            approval_count=approval_count,
            changes_requested=changes_requested,
        )

    def find_open_pr(self, head_branch: str, base_branch: str) -> int | None:
        """
        Return the PR number of an existing open PR from head → base, or None.
        Used for idempotent PR creation.
        """
        params = urllib.parse.urlencode({
            "head":  f"{self._owner}:{head_branch}",
            "base":  base_branch,
            "state": "open",
            "per_page": 1,
        })
        data = self._get(f"/pulls?{params}")
        if isinstance(data, list) and data:
            return data[0]["number"]
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_check_runs(self, commit_sha: str) -> list[CheckRunResult]:
        """Fetch all check runs for a commit SHA."""
        params = urllib.parse.urlencode({"per_page": 100})
        try:
            data = self._get(f"/commits/{commit_sha}/check-runs?{params}")
        except GitHubError as exc:
            log.warning("github.check_runs_fetch_failed", sha=commit_sha, error=str(exc))
            return []

        runs = []
        for item in data.get("check_runs", []):
            conclusion_raw = item.get("conclusion") or "pending"
            try:
                conclusion = PRCheckConclusion(conclusion_raw)
            except ValueError:
                conclusion = PRCheckConclusion.PENDING
            runs.append(CheckRunResult(
                name=item["name"],
                conclusion=conclusion,
                url=item.get("html_url"),
            ))
        return runs

    def _fetch_review_summary(self, pr_number: int) -> tuple[int, bool]:
        """
        Return (approval_count, changes_requested) by inspecting reviews.
        Uses the latest review state per reviewer (GitHub's own logic).
        """
        params = urllib.parse.urlencode({"per_page": 100})
        try:
            reviews = self._get(f"/pulls/{pr_number}/reviews?{params}")
        except GitHubError as exc:
            log.warning("github.reviews_fetch_failed", pr_number=pr_number, error=str(exc))
            return 0, False

        # Track latest state per reviewer login
        latest: dict[str, str] = {}
        for review in reviews:
            login = review.get("user", {}).get("login", "unknown")
            state = review.get("state", "COMMENTED")
            if state != "COMMENTED":   # COMMENTED does not count as a vote
                latest[login] = state

        approvals         = sum(1 for s in latest.values() if s == "APPROVED")
        changes_requested = any(s == "CHANGES_REQUESTED" for s in latest.values())
        return approvals, changes_requested

    def _get(self, path: str) -> Any:
        return self._request("GET", path, body=None)

    def _post(self, path: str, payload: dict) -> Any:
        return self._request("POST", path, body=payload)

    def _request(self, method: str, path: str, body: dict | None) -> Any:
        url = self._base + path if not path.startswith("http") else path
        data = json.dumps(body).encode() if body is not None else None

        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization":         f"Bearer {self._token}",
                "Accept":                _ACCEPT,
                "X-GitHub-Api-Version":  _API_VER,
                "Content-Type":          "application/json",
                "User-Agent":            "agent-orchestrator/1.0",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw else {}

        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode(errors="replace")
            log.error(
                "github.http_error",
                method=method,
                url=url,
                status=exc.code,
                body=body_text[:500],
            )
            if exc.code == 429 or exc.headers.get("X-RateLimit-Remaining") == "0":
                reset_at = exc.headers.get("X-RateLimit-Reset", "unknown")
                raise GitHubRateLimitError(
                    f"GitHub rate limit exceeded. Resets at {reset_at}.",
                    status_code=429,
                ) from exc
            raise GitHubError(
                f"GitHub API {method} {url} failed with HTTP {exc.code}: {body_text[:200]}",
                status_code=exc.code,
            ) from exc

        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            log.error("github.network_error", method=method, url=url, error=str(exc))
            raise GitHubError(f"Network error calling GitHub API: {exc}") from exc


# ---------------------------------------------------------------------------
# Stub adapter for dry-run / tests
# ---------------------------------------------------------------------------


class StubGitHubClient(GitHubPort):
    """
    In-memory stub for CI tests and dry-run mode.

    Behaviour:
      - create_pr() auto-increments a PR counter and stores the PR.
      - get_pr_info() returns the stored state (mutate via set_pr_state()).
      - find_open_pr() scans stored PRs.
      - set_pr_state() lets tests simulate CI outcomes.
    """

    def __init__(self) -> None:
        self._counter = 0
        self._prs: dict[int, dict] = {}

    def create_pr(
        self,
        *,
        head_branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> int:
        self._counter += 1
        num = self._counter
        self._prs[num] = {
            "pr_number":   num,
            "status":      PRStatus.OPEN,
            "head_branch": head_branch,
            "base_branch": base_branch,
            "head_sha":    f"fake-sha-{num:04d}",
            "html_url":    f"https://github.com/example/repo/pull/{num}",
            "title":       title,
            "check_runs":  [],
            "approval_count":    0,
            "changes_requested": False,
        }
        log.info("stub_github.pr_created", pr_number=num, head=head_branch)
        return num

    def get_pr_info(self, pr_number: int) -> PRInfo:
        if pr_number not in self._prs:
            raise GitHubError(f"PR #{pr_number} not found in stub", status_code=404)
        state = self._prs[pr_number]
        return PRInfo(**state)

    def find_open_pr(self, head_branch: str, base_branch: str) -> int | None:
        for num, pr in self._prs.items():
            if (
                pr["head_branch"] == head_branch
                and pr["base_branch"] == base_branch
                and pr["status"] == PRStatus.OPEN
            ):
                return num
        return None

    # ------------------------------------------------------------------
    # Test helpers — not part of the GitHubPort contract
    # ------------------------------------------------------------------

    def set_pr_state(
        self,
        pr_number: int,
        *,
        status: PRStatus | None = None,
        head_sha: str | None = None,
        check_runs: list[CheckRunResult] | None = None,
        approval_count: int | None = None,
        changes_requested: bool | None = None,
    ) -> None:
        """Mutate stored PR state so tests can simulate GitHub transitions."""
        pr = self._prs[pr_number]
        if status is not None:
            pr["status"] = status
        if head_sha is not None:
            pr["head_sha"] = head_sha
        if check_runs is not None:
            pr["check_runs"] = check_runs
        if approval_count is not None:
            pr["approval_count"] = approval_count
        if changes_requested is not None:
            pr["changes_requested"] = changes_requested

    def approve_pr(self, pr_number: int) -> None:
        """Convenience: set approval_count=1, changes_requested=False."""
        self.set_pr_state(pr_number, approval_count=1, changes_requested=False)

    def pass_all_checks(self, pr_number: int, check_names: list[str]) -> None:
        """Convenience: mark all named checks as SUCCESS."""
        runs = [
            CheckRunResult(name=n, conclusion=PRCheckConclusion.SUCCESS)
            for n in check_names
        ]
        self.set_pr_state(pr_number, check_runs=runs)

    def merge_pr(self, pr_number: int, merge_sha: str = "merge-sha-0000") -> None:
        """Convenience: simulate a merged PR."""
        self.set_pr_state(pr_number, status=PRStatus.MERGED, head_sha=merge_sha)

    def close_pr(self, pr_number: int) -> None:
        """Convenience: simulate a closed (unmerged) PR."""
        self.set_pr_state(pr_number, status=PRStatus.CLOSED)
