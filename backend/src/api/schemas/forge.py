"""
src/api/schemas/forge.py — PR-window response DTOs.

Serialization-only projections mapped from the domain forge value objects.
Topology only for the graph; layout is computed client-side.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from src.domain.value_objects.forge import (
    CommitGraph,
    CommitNode,
    ForgeCapabilities,
    PullRequest,
)


class PersonResponse(BaseModel):
    name: str
    email: str | None = None
    login: str | None = None
    avatar_url: str | None = None


class CommitNodeResponse(BaseModel):
    sha: str
    parents: list[str]
    summary: str
    author: PersonResponse
    committer: PersonResponse
    authored_at: datetime
    committed_at: datetime
    refs: list[str]
    pr_number: int | None

    @classmethod
    def from_domain(cls, c: CommitNode) -> "CommitNodeResponse":
        return cls(
            sha=c.sha,
            parents=list(c.parents),
            summary=c.summary,
            author=PersonResponse(**c.author.model_dump()),
            committer=PersonResponse(**c.committer.model_dump()),
            authored_at=c.authored_at,
            committed_at=c.committed_at,
            refs=list(c.refs),
            pr_number=c.pr_number,
        )


class CommitGraphResponse(BaseModel):
    nodes: list[CommitNodeResponse]
    source: str
    truncated: bool
    head_sha: str | None
    dangling_parents: list[str]

    @classmethod
    def from_domain(cls, g: CommitGraph) -> "CommitGraphResponse":
        return cls(
            nodes=[CommitNodeResponse.from_domain(n) for n in g.nodes],
            source=g.source.value,
            truncated=g.truncated,
            head_sha=g.head_sha,
            dangling_parents=sorted(g.dangling_parents),
        )


class PullRequestResponse(BaseModel):
    number: int
    title: str
    state: str
    head_ref: str
    base_ref: str
    head_sha: str
    author: PersonResponse
    review_state: str
    checks: str
    requested_reviewers: list[str]
    is_mergeable: bool | None
    created_at: datetime
    updated_at: datetime
    source: str

    @classmethod
    def from_domain(cls, p: PullRequest) -> "PullRequestResponse":
        return cls(
            number=p.number,
            title=p.title,
            state=p.state.value,
            head_ref=p.head_ref,
            base_ref=p.base_ref,
            head_sha=p.head_sha,
            author=PersonResponse(**p.author.model_dump()),
            review_state=p.review_state.value,
            checks=p.checks.value,
            requested_reviewers=list(p.requested_reviewers),
            is_mergeable=p.is_mergeable,
            created_at=p.created_at,
            updated_at=p.updated_at,
            source=p.source.value,
        )


class ForgeCapabilitiesResponse(BaseModel):
    source: str
    supports_prs: bool
    supports_reviews: bool
    supports_checks: bool

    @classmethod
    def from_domain(cls, c: ForgeCapabilities) -> "ForgeCapabilitiesResponse":
        return cls(
            source=c.source.value,
            supports_prs=c.supports_prs,
            supports_reviews=c.supports_reviews,
            supports_checks=c.supports_checks,
        )
