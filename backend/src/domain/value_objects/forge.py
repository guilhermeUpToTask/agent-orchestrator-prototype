"""
src/domain/value_objects/forge.py — git-forge projection value objects.

Read-only projections of git/forge state for the PR window. Three data tiers:
  1. commit topology (both GitHub and local git can populate) — CommitNode;
  2. PRs / reviews / checks (GitHub only) — PullRequest;
  3. identity that differs by source (GitHub login vs git name+email) — Person.

These are projections: immutable, never written to the task tables. A ``source``
discriminator + ``ForgeCapabilities`` let a local-git adapter advertise the gaps
(no PRs) instead of throwing.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class DataSource(str, Enum):
    GITHUB = "github"
    LOCAL_GIT = "local_git"


class Person(_Frozen):
    name: str
    email: str | None = None
    login: str | None = None        # GitHub only
    avatar_url: str | None = None   # GitHub only


class CommitNode(_Frozen):
    sha: str                        # full 40-char always
    parents: tuple[str, ...]        # 0 = root, 1 = normal, 2+ = merge
    summary: str
    author: Person
    committer: Person
    authored_at: datetime
    committed_at: datetime
    refs: tuple[str, ...] = ()
    pr_number: int | None = None    # None from local git


class CommitGraph(_Frozen):
    nodes: tuple[CommitNode, ...]
    source: DataSource
    truncated: bool
    head_sha: str | None
    dangling_parents: frozenset[str] = frozenset()


class ReviewState(str, Enum):
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    COMMENTED = "commented"
    PENDING = "pending"
    DISMISSED = "dismissed"


class CheckState(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    PENDING = "pending"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


class PrState(str, Enum):
    OPEN = "open"
    DRAFT = "draft"
    MERGED = "merged"
    CLOSED = "closed"


class PullRequest(_Frozen):
    number: int
    title: str
    state: PrState
    head_ref: str
    base_ref: str
    head_sha: str
    author: Person
    review_state: ReviewState
    checks: CheckState
    requested_reviewers: tuple[str, ...]
    is_mergeable: bool | None        # None = not yet known ("checking…")
    created_at: datetime
    updated_at: datetime
    source: DataSource


class ForgeCapabilities(_Frozen):
    source: DataSource
    supports_prs: bool
    supports_reviews: bool
    supports_checks: bool
