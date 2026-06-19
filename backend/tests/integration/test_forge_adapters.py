"""Tests for the git-forge adapters (Phase 6).

LocalGitForge runs against a real temp repo (no network). GitHubForge is driven
through a stubbed ``_get`` so no live GitHub calls are made — assertions check
shape/type conformance and the tier mapping, not value equality.
"""
from __future__ import annotations

import pytest

from src.app.errors import ResourceNotFoundException
from src.domain.value_objects.forge import CheckState, DataSource, PrState, ReviewState
from src.infra.forge.github import GitHubForge
from src.infra.forge.local_git import LocalGitForge


# ── LocalGitForge ───────────────────────────────────────────────────────────────

@pytest.fixture
def git_repo(tmp_path):
    from git import Repo

    repo = Repo.init(tmp_path)
    repo.config_writer().set_value("user", "name", "Tester").release()
    repo.config_writer().set_value("user", "email", "t@example.com").release()
    (tmp_path / "a.txt").write_text("one")
    repo.index.add(["a.txt"])
    c1 = repo.index.commit("first")
    (tmp_path / "b.txt").write_text("two")
    repo.index.add(["b.txt"])
    c2 = repo.index.commit("second")
    return tmp_path, c1, c2


class TestLocalGitForge:
    def test_capabilities_advertise_no_prs(self, git_repo) -> None:
        path, *_ = git_repo
        caps = LocalGitForge(str(path)).capabilities()
        assert caps.source == DataSource.LOCAL_GIT
        assert caps.supports_prs is False

    def test_commit_graph_topology(self, git_repo) -> None:
        path, c1, c2 = git_repo
        graph = LocalGitForge(str(path)).commit_graph(limit=200)
        assert graph.source == DataSource.LOCAL_GIT
        shas = {n.sha for n in graph.nodes}
        assert c1.hexsha in shas and c2.hexsha in shas
        # full 40-char SHAs (no fragmentation)
        assert all(len(n.sha) == 40 for n in graph.nodes)
        head = next(n for n in graph.nodes if n.sha == c2.hexsha)
        assert head.parents == (c1.hexsha,)
        assert graph.head_sha == c2.hexsha

    def test_list_prs_empty_never_raises(self, git_repo) -> None:
        path, *_ = git_repo
        assert LocalGitForge(str(path)).list_prs() == ()

    def test_get_pr_raises_not_found(self, git_repo) -> None:
        path, *_ = git_repo
        with pytest.raises(ResourceNotFoundException):
            LocalGitForge(str(path)).get_pr(1)


# ── GitHubForge (stubbed transport) ──────────────────────────────────────────────

class _StubGitHub(GitHubForge):
    def __init__(self, responses: dict[str, object]) -> None:
        super().__init__(token="t", owner="o", repo="r")
        self._responses = responses

    def _get(self, path: str, params=None):  # type: ignore[override]
        for key, value in self._responses.items():
            if path.endswith(key):
                return value
        raise AssertionError(f"unexpected path: {path}")


def _commit(sha: str, parent: str | None):
    return {
        "sha": sha,
        "parents": ([{"sha": parent}] if parent else []),
        "commit": {
            "message": "msg\n\nbody",
            "author": {"name": "A", "email": "a@x", "date": "2026-01-01T00:00:00Z"},
            "committer": {"name": "A", "email": "a@x", "date": "2026-01-01T00:00:00Z"},
        },
        "author": {"login": "alice", "avatar_url": "http://x/a.png"},
        "committer": {"login": "alice"},
    }


class TestGitHubForge:
    def test_commit_graph_maps_tiers(self) -> None:
        forge = _StubGitHub({
            "/commits": [_commit("a" * 40, "b" * 40), _commit("b" * 40, None)],
        })
        graph = forge.commit_graph(limit=100)
        assert graph.source == DataSource.GITHUB
        assert all(len(n.sha) == 40 for n in graph.nodes)
        head = graph.nodes[0]
        assert head.author.login == "alice"  # tier-3 github identity
        assert head.summary == "msg"

    def test_get_pr_enriches_review_and_checks(self) -> None:
        forge = _StubGitHub({
            "/pulls/7": {
                "number": 7, "title": "Feat", "state": "open", "draft": False,
                "merged_at": None, "mergeable": True,
                "head": {"ref": "feature", "sha": "c" * 40},
                "base": {"ref": "main"},
                "user": {"login": "bob", "avatar_url": "u"},
                "requested_reviewers": [{"login": "carol"}],
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-02T00:00:00Z",
            },
            "/pulls/7/reviews": [{"user": {"login": "carol"}, "state": "APPROVED"}],
            f"/commits/{'c' * 40}/check-runs": {
                "check_runs": [{"status": "completed", "conclusion": "success"}]
            },
        })
        pr = forge.get_pr(7)
        assert pr.state == PrState.OPEN
        assert pr.review_state == ReviewState.APPROVED
        assert pr.checks == CheckState.SUCCESS
        assert pr.is_mergeable is True
        assert pr.requested_reviewers == ("carol",)

    def test_pr_state_merged_vs_closed(self) -> None:
        forge = _StubGitHub({
            "/pulls/9": {
                "number": 9, "title": "X", "state": "closed",
                "merged_at": "2026-01-03T00:00:00Z", "mergeable": None,
                "head": {"ref": "f", "sha": "d" * 40}, "base": {"ref": "main"},
                "user": {"login": "bob"}, "requested_reviewers": [],
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-02T00:00:00Z",
            },
            "/pulls/9/reviews": [],
            f"/commits/{'d' * 40}/check-runs": {"check_runs": []},
        })
        pr = forge.get_pr(9)
        assert pr.state == PrState.MERGED  # closed + merged_at -> merged
        assert pr.is_mergeable is None     # tri-state "checking…"
        assert pr.checks == CheckState.UNKNOWN
