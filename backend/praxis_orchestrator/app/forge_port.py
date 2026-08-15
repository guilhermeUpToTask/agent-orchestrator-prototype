"""The Forge port: pushing a verified cycle branch to a hosting service and
opening a pull request for it.

Deliberately NOT a domain concept — the frozen domain never sees these types —
and deliberately not part of the Workspace port, whose contract is local git
only. An authenticated push spends the forge's credential, not the workspace's,
so the two operations that need that credential live together here.

Adapters live in infra: `NoForge` (praxis_orchestrator/infra/forge/no_forge.py)
is the PERMANENT fallback, not a placeholder — an installation with no token
must keep working and record the disposition an operator typed, exactly as it
did before this port existed. `GitHubForge` plugs in beside it without any
caller changing.

Two hard scope limits, enforced by having no method for either: this port opens
a pull request and cannot merge one, and it pushes one named branch and cannot
touch a default branch.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from praxis_orchestrator.domain.errors.base import BaseAppException


class ForgeError(BaseAppException):
    """Base for every forge failure. Subclasses carry the stable code."""

    code = "FORGE_REQUEST_FAILED"


class ForgeNotConfiguredError(ForgeError):
    """Asked to really open a pull request with no forge bound to the project."""

    code = "FORGE_NOT_CONFIGURED"


class ForgeAuthFailedError(ForgeError):
    """The token was rejected, or cannot push to the repository."""

    code = "FORGE_AUTH_FAILED"


class ForgeRepoNotFoundError(ForgeError):
    """`owner/repo` does not resolve for this token."""

    code = "FORGE_REPO_NOT_FOUND"


class ForgePushFailedError(ForgeError):
    """The push reached the remote and was refused."""

    code = "FORGE_PUSH_FAILED"


class ForgeRequestFailedError(ForgeError):
    """The forge API failed or was unreachable."""

    code = "FORGE_REQUEST_FAILED"


@dataclass(frozen=True)
class PullRequestRef:
    url: str
    number: int


@runtime_checkable
class ForgePort(Protocol):
    def push_branch(self, repo: Path, branch: str) -> None:
        """Push exactly `branch`. Never a default branch, never a force push."""
        ...

    def open_pull_request(
        self, *, head: str, base: str, title: str, body: str
    ) -> PullRequestRef:
        """Open a pull request. There is deliberately no merge counterpart."""
        ...
