"""Tests for FallbackForge degrade-to-local behaviour (B4)."""
from __future__ import annotations

import pytest

from src.app.errors import ExternalServiceException
from src.domain.ports.forge import GitForgePort
from src.domain.value_objects.forge import (
    CommitGraph,
    DataSource,
    ForgeCapabilities,
    PrState,
    PullRequest,
)
from src.infra.forge.fallback import FallbackForge


class _BoomGitHub(GitForgePort):
    """Primary that always fails upstream."""

    def capabilities(self) -> ForgeCapabilities:
        return ForgeCapabilities(
            source=DataSource.GITHUB, supports_prs=True,
            supports_reviews=True, supports_checks=True,
        )

    def commit_graph(self, *, branch=None, limit=200) -> CommitGraph:
        raise ExternalServiceException("boom", code="GITHUB_ERROR")

    def list_prs(self, *, state=None) -> tuple[PullRequest, ...]:
        raise ExternalServiceException("boom", code="GITHUB_ERROR")

    def get_pr(self, number: int) -> PullRequest:
        raise ExternalServiceException("boom", code="GITHUB_ERROR")

    def pending_reviews(self, *, reviewer: str) -> tuple[PullRequest, ...]:
        raise ExternalServiceException("boom", code="GITHUB_ERROR")


class _Local(GitForgePort):
    """Fallback marker."""

    def capabilities(self) -> ForgeCapabilities:
        return ForgeCapabilities(
            source=DataSource.LOCAL_GIT, supports_prs=False,
            supports_reviews=False, supports_checks=False,
        )

    def commit_graph(self, *, branch=None, limit=200) -> CommitGraph:
        return CommitGraph(nodes=(), source=DataSource.LOCAL_GIT, truncated=False, head_sha=None)

    def list_prs(self, *, state=None) -> tuple[PullRequest, ...]:
        return ()

    def get_pr(self, number: int) -> PullRequest:
        raise AssertionError("get_pr must not fall back")

    def pending_reviews(self, *, reviewer: str) -> tuple[PullRequest, ...]:
        return ()


@pytest.fixture
def forge():
    return FallbackForge(primary=_BoomGitHub(), fallback=_Local())


def test_commit_graph_degrades_to_local(forge) -> None:
    g = forge.commit_graph()
    assert g.source == DataSource.LOCAL_GIT


def test_list_prs_degrades_to_local(forge) -> None:
    assert forge.list_prs(state=PrState.OPEN) == ()


def test_pending_reviews_degrades_to_local(forge) -> None:
    assert forge.pending_reviews(reviewer="alice") == ()


def test_get_pr_does_not_fall_back(forge) -> None:
    # A specific PR has no local equivalent — the upstream error must surface.
    with pytest.raises(ExternalServiceException):
        forge.get_pr(7)


def test_capabilities_from_primary(forge) -> None:
    assert forge.capabilities().source == DataSource.GITHUB
