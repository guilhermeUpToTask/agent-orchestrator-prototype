"""
src/infra/forge/fallback.py — GitHub-primary, local-git-fallback forge.

Keeps the API routers thin: the degrade-to-local-git-on-GitHub-failure policy
lives here, not in a handler. Read operations that GitHub can't serve (rate
limit, outage) fall back to local git, which serves commit topology and empty
PR collections. ``get_pr`` does not fall back — a specific PR has no local
equivalent.
"""
from __future__ import annotations

import structlog

from src.app.errors import ExternalServiceException
from src.domain.ports.forge import GitForgePort
from src.domain.value_objects.forge import (
    CommitGraph,
    ForgeCapabilities,
    PrState,
    PullRequest,
)

log = structlog.get_logger(__name__)


class FallbackForge(GitForgePort):
    def __init__(self, primary: GitForgePort, fallback: GitForgePort) -> None:
        self._primary = primary
        self._fallback = fallback

    def capabilities(self) -> ForgeCapabilities:
        return self._primary.capabilities()

    def commit_graph(self, *, branch: str | None = None, limit: int = 200) -> CommitGraph:
        try:
            return self._primary.commit_graph(branch=branch, limit=limit)
        except ExternalServiceException as exc:
            log.warning("forge.degraded_to_local", op="commit_graph", error=str(exc))
            return self._fallback.commit_graph(branch=branch, limit=limit)

    def list_prs(self, *, state: PrState | None = None) -> tuple[PullRequest, ...]:
        try:
            return self._primary.list_prs(state=state)
        except ExternalServiceException as exc:
            log.warning("forge.degraded_to_local", op="list_prs", error=str(exc))
            return self._fallback.list_prs(state=state)

    def get_pr(self, number: int) -> PullRequest:
        return self._primary.get_pr(number)

    def pending_reviews(self, *, reviewer: str) -> tuple[PullRequest, ...]:
        try:
            return self._primary.pending_reviews(reviewer=reviewer)
        except ExternalServiceException as exc:
            log.warning("forge.degraded_to_local", op="pending_reviews", error=str(exc))
            return self._fallback.pending_reviews(reviewer=reviewer)
