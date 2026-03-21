"""
src/infra/github/github_client.py — GitHubPort adapter using the GitHub REST API.

Uses only the stdlib ``urllib`` stack plus the ``Authorization`` header so
there is no required third-party HTTP dependency (PyGithub, httpx, etc.).
A requests-based implementation can easily replace this without touching
anything outside this file — it just needs to satisfy GitHubPort.

Authentication:
  Set GITHUB_TOKEN in the environment (or pass token= to the constructor).
  The token must have pull_request:write and checks:read scopes for a
  private repository, or be a fine-grained PAT with the matching resource
  permissions.

Rate limiting:
  GitHub's primary REST API allows 5 000 requests/hour for authenticated
  calls. The reconciler polling loop (default: 60 s, ~1 440 calls/hour for
  a single goal) is well within this budget. For large fleets of concurrent
  goals, consider increasing the polling interval or batching list calls.

Retry policy:
  We retry 429 (secondary rate limit) and 502/503 responses up to
  MAX_RETRIES times with exponential back-off. All other errors propagate
  immediately as GitHubError.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlencode

import structlog

from src.domain.ports.github import GitHubError, GitHubPort, GitHubRateLimitError
from src.domain.value_objects.pr import (
    CheckRunResult,
    PRCheckConclusion,
    PRInfo,
    PRStatus,
)

log = structlog.get_logger(__name__)

MAX_RETRIES = 3
BASE_BACKOFF = 2.0  # seconds; doubles on each retry


class GitHubClient(GitHubPort):
    """
    GitHub REST API v3 adapter.

    Constructor args:
      owner:  GitHub organisation or user name  (e.g. "acme-corp")
      repo:   Repository name                   (e.g. "backend-api")
      token:  Personal access token; falls back to GITHUB_TOKEN env var.
      base_url: Override for GitHub Enterprise; defaults to api.github.com.
    """

    def __init__(
        self,
        owner: str,
        repo: str,
        token: str | None = None,
        base_url: str = "https://api.github.com",
    ) -> None:
        self._owner    = owner
        self._repo     = repo
        self._token    = token or os.environ.get("GITHUB_TOKEN", "")
        self._base_url = base_url.rstrip("/")

        if not self._token:
            raise GitHubError(
                "GitHub token not configured. "
                "Set GITHUB_TOKEN or pass token= to GitHubClient."
            )

    # ------------------------------------------------------------------
    # GitHubPort implementation
    # ------------------------------------------------------------------

    def create_pr(
        self,
        *,
        head_branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> int:
        """Open a PR from head_branch → base_branch. Returns the PR number."""
        # Idempotency guard: return existing PR if one is already open
        existing = self.find_open_pr(head_branch, base_branch)
        if existing is not None:
            log.info(
                "github.pr_already_exists",
                pr_number=existing,
                head=head_branch,
                base=base_branch,
            )
            return existing

        payload = {
            "title": title,
            "body": body,
            "head": head_branch,
            "base": base_branch,
        }
        url  = self._repo_url("pulls")
        data = self._post(url, payload)
        pr_number: int = data["number"]
        log.info(
            "github.pr_created",
            pr_number=pr_number,
            url=data.get("html_url"),
        )
        return pr_number

    def get_pr_info(self, pr_number: int) -> PRInfo:
        """Fetch the current state of a PR including check runs and reviews."""
        pr_data    = self._get(self._repo_url(f"pulls/{pr_number}"))
        check_runs = self._get_check_runs(pr_data["head"]["sha"])
        reviews    = self._get_reviews(pr_number)

        status = _parse_pr_status(pr_data)

        approval_count     = sum(1 for r in reviews if r.get("state") == "APPROVED")
        changes_requested  = any(r.get("state") == "CHANGES_REQUESTED" for r in reviews)

        # Evaluate required checks
        checks_passed = all(cr.passed for cr in check_runs) if check_runs else True

        return PRInfo(
            pr_number=pr_number,
            status=status,
            head_branch=pr_data["head"]["ref"],
            base_branch=pr_data["base"]["ref"],
            head_sha=pr_data["head"]["sha"],
            html_url=pr_data["html_url"],
            title=pr_data["title"],
            check_runs=check_runs,
            approval_count=approval_count,
            changes_requested=changes_requested,
        )

    def find_open_pr(self, head_branch: str, base_branch: str) -> int | None:
        """Return the number of an existing open PR, or None."""
        params = urlencode({
            "state": "open",
            "head":  f"{self._owner}:{head_branch}",
            "base":  base_branch,
            "per_page": 1,
        })
        url  = self._repo_url(f"pulls?{params}")
        data = self._get(url)
        if data and isinstance(data, list):
            return data[0]["number"]
        return None

    # ------------------------------------------------------------------
    # Internal: GitHub API call helpers
    # ------------------------------------------------------------------

    def _repo_url(self, path: str) -> str:
        return f"{self._base_url}/repos/{self._owner}/{self._repo}/{path}"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept":        "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type":  "application/json",
            "User-Agent":    "agent-orchestrator/1.0",
        }

    def _get(self, url: str) -> Any:
        return self._request("GET", url, body=None)

    def _post(self, url: str, payload: dict) -> Any:
        return self._request("POST", url, body=payload)

    def _request(self, method: str, url: str, body: dict | None) -> Any:
        encoded = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url,
            data=encoded,
            headers=self._headers(),
            method=method,
        )
        for attempt in range(MAX_RETRIES):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read().decode()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                status = exc.code
                body_text = exc.read().decode(errors="replace")
                log.warning(
                    "github.http_error",
                    method=method,
                    url=url,
                    status=status,
                    attempt=attempt,
                    body=body_text[:200],
                )
                if status == 429:
                    # Secondary rate limit — respect Retry-After header if present
                    retry_after = float(exc.headers.get("Retry-After", BASE_BACKOFF * (2 ** attempt)))
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(retry_after)
                        continue
                    raise GitHubRateLimitError(
                        f"GitHub rate limit exceeded: {body_text}", status_code=status
                    )
                if status in (502, 503) and attempt < MAX_RETRIES - 1:
                    time.sleep(BASE_BACKOFF * (2 ** attempt))
                    continue
                raise GitHubError(
                    f"GitHub API {method} {url} returned {status}: {body_text}",
                    status_code=status,
                )
            except OSError as exc:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(BASE_BACKOFF * (2 ** attempt))
                    continue
                raise GitHubError(f"Network error calling GitHub: {exc}") from exc
        raise GitHubError(f"Exhausted {MAX_RETRIES} retries for {method} {url}")

    # ------------------------------------------------------------------
    # Internal: check runs + reviews
    # ------------------------------------------------------------------

    def _get_check_runs(self, commit_sha: str) -> list[CheckRunResult]:
        """Fetch all check runs for a commit SHA."""
        url  = self._repo_url(f"commits/{commit_sha}/check-runs?per_page=100")
        data = self._get(url)
        runs = data.get("check_runs", []) if isinstance(data, dict) else []
        results: list[CheckRunResult] = []
        for run in runs:
            conclusion_raw = run.get("conclusion") or "pending"
            try:
                conclusion = PRCheckConclusion(conclusion_raw)
            except ValueError:
                conclusion = PRCheckConclusion.PENDING
            results.append(CheckRunResult(
                name=run["name"],
                conclusion=conclusion,
                url=run.get("html_url"),
            ))
        return results

    def _get_reviews(self, pr_number: int) -> list[dict]:
        """Fetch all submitted reviews for a PR."""
        url  = self._repo_url(f"pulls/{pr_number}/reviews?per_page=100")
        data = self._get(url)
        return data if isinstance(data, list) else []


# ---------------------------------------------------------------------------
# Dry-run / stub adapter (tests, CI without a real GitHub token)
# ---------------------------------------------------------------------------


class DryRunGitHubClient(GitHubPort):
    """
    In-memory GitHub stub for dry-run mode and unit tests.

    Simulates PR creation and state progression without touching the real API.
    Tests inject PR state via inject_pr_state().
    """

    def __init__(self) -> None:
        self._next_pr = 1
        self._prs: dict[int, PRInfo] = {}
        self._branch_to_pr: dict[str, int] = {}

    # GitHubPort ----------------------------------------------------------

    def create_pr(
        self,
        *,
        head_branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> int:
        existing = self.find_open_pr(head_branch, base_branch)
        if existing is not None:
            return existing

        pr_number = self._next_pr
        self._next_pr += 1
        pr = PRInfo(
            pr_number=pr_number,
            status=PRStatus.OPEN,
            head_branch=head_branch,
            base_branch=base_branch,
            head_sha=f"sha-{pr_number}-000",
            html_url=f"https://github.com/dry-run/repo/pull/{pr_number}",
            title=title,
            check_runs=[],
            approval_count=0,
            changes_requested=False,
        )
        self._prs[pr_number] = pr
        self._branch_to_pr[head_branch] = pr_number
        log.info("dry_run_github.pr_created", pr_number=pr_number, head=head_branch)
        return pr_number

    def get_pr_info(self, pr_number: int) -> PRInfo:
        if pr_number not in self._prs:
            raise GitHubError(f"PR #{pr_number} not found in dry-run state")
        return self._prs[pr_number]

    def find_open_pr(self, head_branch: str, base_branch: str) -> int | None:
        pr_number = self._branch_to_pr.get(head_branch)
        if pr_number is None:
            return None
        pr = self._prs.get(pr_number)
        if pr and pr.status == PRStatus.OPEN:
            return pr_number
        return None

    # Test helpers ---------------------------------------------------------

    def inject_pr_state(self, pr_number: int, pr: PRInfo) -> None:
        """Replace the stored PRInfo for a PR number (test helper)."""
        self._prs[pr_number] = pr

    def simulate_merge(self, pr_number: int) -> None:
        """Mark a PR as merged (test helper)."""
        pr = self._prs[pr_number]
        self._prs[pr_number] = PRInfo(
            **{**pr.model_dump(), "status": PRStatus.MERGED}
        )

    def simulate_close(self, pr_number: int) -> None:
        """Mark a PR as closed without merging (test helper)."""
        pr = self._prs[pr_number]
        self._prs[pr_number] = PRInfo(
            **{**pr.model_dump(), "status": PRStatus.CLOSED}
        )

    def simulate_checks_passed(self, pr_number: int) -> None:
        """Simulate all checks passing (test helper)."""
        from src.domain.value_objects.pr import CheckRunResult, PRCheckConclusion
        pr = self._prs[pr_number]
        checks = [
            CheckRunResult(name=cr.name, conclusion=PRCheckConclusion.SUCCESS)
            for cr in pr.check_runs
        ] or [CheckRunResult(name="ci", conclusion=PRCheckConclusion.SUCCESS)]
        self._prs[pr_number] = PRInfo(
            **{**pr.model_dump(), "check_runs": [c.model_dump() for c in checks]}
        )

    def simulate_approval(self, pr_number: int, count: int = 1) -> None:
        """Simulate approvals (test helper)."""
        pr = self._prs[pr_number]
        self._prs[pr_number] = PRInfo(
            **{**pr.model_dump(), "approval_count": count, "changes_requested": False}
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_pr_status(pr_data: dict) -> PRStatus:
    """Derive PRStatus from raw GitHub PR JSON."""
    if pr_data.get("merged_at"):
        return PRStatus.MERGED
    state = pr_data.get("state", "open")
    if state == "closed":
        return PRStatus.CLOSED
    return PRStatus.OPEN
