"""
src/infra/forge/local_git.py — local-git fallback adapter for GitForgePort.

Reads commit topology from a local repository via GitPython. It has no concept
of PRs/reviews/checks, so it advertises those gaps via ``capabilities()`` and
returns empty PR collections — it never raises for an unsupported operation
(except get_pr, which is a genuine not-found). SHAs are always full 40-char so
the DAG never fragments against GitHub-sourced nodes.
"""
from __future__ import annotations

import structlog

from src.app.errors import ResourceNotFoundException
from src.domain.ports.forge import GitForgePort
from src.domain.value_objects.forge import (
    CommitGraph,
    CommitNode,
    DataSource,
    ForgeCapabilities,
    Person,
    PrState,
    PullRequest,
)

log = structlog.get_logger(__name__)


class LocalGitForge(GitForgePort):
    def __init__(self, repo_path: str) -> None:
        self._repo_path = repo_path

    def capabilities(self) -> ForgeCapabilities:
        return ForgeCapabilities(
            source=DataSource.LOCAL_GIT,
            supports_prs=False,
            supports_reviews=False,
            supports_checks=False,
        )

    def commit_graph(self, *, branch: str | None = None, limit: int = 200) -> CommitGraph:
        from git import Repo  # lazy import — keep GitPython out of the import path

        repo = Repo(self._repo_path)
        rev = branch if branch else None
        commits = list(repo.iter_commits(rev, max_count=limit))

        nodes: list[CommitNode] = []
        present: set[str] = {c.hexsha for c in commits}
        dangling: set[str] = set()
        for c in commits:
            parents = tuple(p.hexsha for p in c.parents)
            for p in parents:
                if p not in present:
                    dangling.add(p)
            nodes.append(
                CommitNode(
                    sha=c.hexsha,
                    parents=parents,
                    summary=c.summary if isinstance(c.summary, str) else c.summary.decode(),
                    author=Person(name=c.author.name or "", email=c.author.email),
                    committer=Person(name=c.committer.name or "", email=c.committer.email),
                    authored_at=c.authored_datetime,
                    committed_at=c.committed_datetime,
                )
            )

        head_sha: str | None = None
        try:
            head_sha = repo.head.commit.hexsha
        except Exception:  # empty/detached repo has no resolvable HEAD
            head_sha = None

        return CommitGraph(
            nodes=tuple(nodes),
            source=DataSource.LOCAL_GIT,
            truncated=len(commits) >= limit,
            head_sha=head_sha,
            dangling_parents=frozenset(dangling),
        )

    def list_prs(self, *, state: PrState | None = None) -> tuple[PullRequest, ...]:
        return ()  # local git has no PRs — advertise via capabilities, never raise

    def get_pr(self, number: int) -> PullRequest:
        raise ResourceNotFoundException(
            "Local git has no pull requests", code="PR_NOT_SUPPORTED"
        )

    def pending_reviews(self, *, reviewer: str) -> tuple[PullRequest, ...]:
        return ()
