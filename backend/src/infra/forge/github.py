"""
src/infra/forge/github.py — GitHub adapter for GitForgePort (read-only).

GitHub is the source of truth for PRs/reviews/checks; commit topology comes from
the commits API. Uses stdlib urllib + json (no new dependency), mirroring
``src.infra.github.client``. Upstream failures — including rate-limit 403/429 —
raise ExternalServiceException (never an empty list masquerading as "no data");
the caller degrades to the local-git fallback.

SHAs are normalized to full 40-char so the DAG never fragments. ``list_prs``
returns cheap projections (review/checks unknown); ``get_pr`` enriches a single
PR with reviews + check-runs.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any

import structlog

from src.app.errors import ExternalServiceException, ResourceNotFoundException
from src.domain.ports.forge import GitForgePort
from src.domain.value_objects.forge import (
    CheckState,
    CommitGraph,
    CommitNode,
    DataSource,
    ForgeCapabilities,
    Person,
    PrState,
    PullRequest,
    ReviewState,
)

log = structlog.get_logger(__name__)

_GITHUB_API = "https://api.github.com"
_ACCEPT = "application/vnd.github+json"
_API_VER = "2022-11-28"


def _parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime.fromtimestamp(0)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class GitHubForge(GitForgePort):
    def __init__(self, token: str, owner: str, repo: str, timeout_s: int = 15) -> None:
        self._token = token
        self._owner = owner
        self._repo = repo
        self._timeout = timeout_s

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{_GITHUB_API}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {self._token}")
        req.add_header("Accept", _ACCEPT)
        req.add_header("X-GitHub-Api-Version", _API_VER)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            remaining = exc.headers.get("X-RateLimit-Remaining") if exc.headers else None
            if exc.code in (403, 429) and remaining == "0":
                raise ExternalServiceException(
                    "GitHub rate limit exceeded", code="GITHUB_RATE_LIMIT"
                ) from exc
            if exc.code == 404:
                raise ResourceNotFoundException(
                    f"GitHub resource not found: {path}", code="GITHUB_NOT_FOUND"
                ) from exc
            raise ExternalServiceException(
                f"GitHub API error {exc.code} on {path}", code="GITHUB_ERROR"
            ) from exc
        except urllib.error.URLError as exc:
            raise ExternalServiceException(
                f"GitHub unreachable: {exc.reason}", code="GITHUB_UNREACHABLE"
            ) from exc

    # ------------------------------------------------------------------
    # Port implementation
    # ------------------------------------------------------------------

    def capabilities(self) -> ForgeCapabilities:
        return ForgeCapabilities(
            source=DataSource.GITHUB,
            supports_prs=True,
            supports_reviews=True,
            supports_checks=True,
        )

    def commit_graph(self, *, branch: str | None = None, limit: int = 200) -> CommitGraph:
        params: dict[str, Any] = {"per_page": min(limit, 100)}
        if branch:
            params["sha"] = branch
        raw = self._get(f"/repos/{self._owner}/{self._repo}/commits", params)
        nodes: list[CommitNode] = []
        present = {c["sha"] for c in raw}
        dangling: set[str] = set()
        for c in raw:
            parents = tuple(p["sha"] for p in c.get("parents", []))
            for p in parents:
                if p not in present:
                    dangling.add(p)
            commit = c.get("commit", {})
            nodes.append(
                CommitNode(
                    sha=c["sha"],
                    parents=parents,
                    summary=(commit.get("message", "").splitlines() or [""])[0],
                    author=_person_from_commit(commit.get("author"), c.get("author")),
                    committer=_person_from_commit(commit.get("committer"), c.get("committer")),
                    authored_at=_parse_dt((commit.get("author") or {}).get("date")),
                    committed_at=_parse_dt((commit.get("committer") or {}).get("date")),
                )
            )
        return CommitGraph(
            nodes=tuple(nodes),
            source=DataSource.GITHUB,
            truncated=len(raw) >= min(limit, 100),
            head_sha=nodes[0].sha if nodes else None,
            dangling_parents=frozenset(dangling),
        )

    def list_prs(self, *, state: PrState | None = None) -> tuple[PullRequest, ...]:
        gh_state = "all"
        if state in (PrState.OPEN, PrState.DRAFT):
            gh_state = "open"
        elif state in (PrState.CLOSED, PrState.MERGED):
            gh_state = "closed"
        raw = self._get(
            f"/repos/{self._owner}/{self._repo}/pulls",
            {"state": gh_state, "per_page": 50},
        )
        prs = [_pr_from_raw(p, review=ReviewState.PENDING, checks=CheckState.UNKNOWN) for p in raw]
        if state is not None:
            prs = [p for p in prs if p.state == state]
        return tuple(prs)

    def get_pr(self, number: int) -> PullRequest:
        raw = self._get(f"/repos/{self._owner}/{self._repo}/pulls/{number}")
        review = self._review_state(number)
        checks = self._check_state(raw.get("head", {}).get("sha"))
        return _pr_from_raw(raw, review=review, checks=checks)

    def pending_reviews(self, *, reviewer: str) -> tuple[PullRequest, ...]:
        return tuple(
            pr for pr in self.list_prs(state=PrState.OPEN)
            if reviewer in pr.requested_reviewers
        )

    # ------------------------------------------------------------------
    # Enrichment
    # ------------------------------------------------------------------

    def _review_state(self, number: int) -> ReviewState:
        raw = self._get(f"/repos/{self._owner}/{self._repo}/pulls/{number}/reviews")
        # Latest review per author wins.
        latest: dict[str, str] = {}
        for r in raw:
            user = (r.get("user") or {}).get("login", "")
            latest[user] = r.get("state", "")
        states = set(latest.values())
        if "CHANGES_REQUESTED" in states:
            return ReviewState.CHANGES_REQUESTED
        if "APPROVED" in states:
            return ReviewState.APPROVED
        if "COMMENTED" in states:
            return ReviewState.COMMENTED
        return ReviewState.PENDING

    def _check_state(self, head_sha: str | None) -> CheckState:
        if not head_sha:
            return CheckState.UNKNOWN
        raw = self._get(f"/repos/{self._owner}/{self._repo}/commits/{head_sha}/check-runs")
        runs = raw.get("check_runs", [])
        if not runs:
            return CheckState.UNKNOWN
        conclusions = {r.get("conclusion") for r in runs}
        statuses = {r.get("status") for r in runs}
        if {"failure", "timed_out", "cancelled", "action_required"} & conclusions:
            return CheckState.FAILURE
        if statuses - {"completed"}:
            return CheckState.PENDING
        if conclusions <= {"success", "neutral", "skipped"} and "success" in conclusions:
            return CheckState.SUCCESS
        return CheckState.NEUTRAL


def _person_from_commit(git_author: dict | None, gh_user: dict | None) -> Person:
    git_author = git_author or {}
    gh_user = gh_user or {}
    return Person(
        name=git_author.get("name", ""),
        email=git_author.get("email"),
        login=gh_user.get("login"),
        avatar_url=gh_user.get("avatar_url"),
    )


def _pr_state(raw: dict) -> PrState:
    if raw.get("merged_at"):
        return PrState.MERGED
    if raw.get("state") == "closed":
        return PrState.CLOSED
    if raw.get("draft"):
        return PrState.DRAFT
    return PrState.OPEN


def _pr_from_raw(raw: dict, *, review: ReviewState, checks: CheckState) -> PullRequest:
    user = raw.get("user") or {}
    return PullRequest(
        number=raw["number"],
        title=raw.get("title", ""),
        state=_pr_state(raw),
        head_ref=(raw.get("head") or {}).get("ref", ""),
        base_ref=(raw.get("base") or {}).get("ref", ""),
        head_sha=(raw.get("head") or {}).get("sha", ""),
        author=Person(name=user.get("login", ""), login=user.get("login"),
                      avatar_url=user.get("avatar_url")),
        review_state=review,
        checks=checks,
        requested_reviewers=tuple(
            r.get("login", "") for r in raw.get("requested_reviewers", [])
        ),
        is_mergeable=raw.get("mergeable"),  # bool | None (None = "checking…")
        created_at=_parse_dt(raw.get("created_at")),
        updated_at=_parse_dt(raw.get("updated_at")),
        source=DataSource.GITHUB,
    )
