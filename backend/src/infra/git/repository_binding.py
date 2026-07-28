"""What a project's `repo_url` must be for a plan to run against it.

Write-time validation, called by the projects router exactly as
`routers/reasoner.py` calls `validate_reasoner_config`. Remote URLs are checked
for syntax only: a create request must not block on a slow or unreachable host,
and a repository reachable now may not be at execution time, so a network probe
would cost a timeout and buy very little.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlparse

from src.infra.errors import ProjectBindingInvalidError

BindingKind = Literal["local", "remote", "scratch"]


@dataclass(frozen=True)
class RepositoryBinding:
    kind: BindingKind
    resolved_path: str | None
    default_branch: str | None


def _local_path(repo_url: str) -> Path | None:
    parsed = urlparse(repo_url)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path))
    if parsed.scheme == "":
        return Path(repo_url).expanduser().resolve()
    return None


def _default_branch(repo: Path) -> str | None:
    for args in (
        ["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
        ["symbolic-ref", "--quiet", "--short", "HEAD"],
    ):
        result = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().removeprefix("origin/")
    return None


def validate_repo_url(repo_url: str | None) -> RepositoryBinding:
    if not repo_url or not repo_url.strip():
        return RepositoryBinding(kind="scratch", resolved_path=None, default_branch=None)

    path = _local_path(repo_url)
    if path is None:
        return RepositoryBinding(kind="remote", resolved_path=None, default_branch=None)

    if not path.exists():
        raise ProjectBindingInvalidError(
            f"repository path {path} does not exist; a plan bound to it could not run "
            "(the workspace refuses to create a repository a project named)"
        )
    if not (path / ".git").exists():
        raise ProjectBindingInvalidError(f"{path} is not a git repository")
    branch = _default_branch(path)
    if branch is None:
        raise ProjectBindingInvalidError(
            f"cannot determine the default branch of {path}; it has no HEAD and no branches"
        )
    return RepositoryBinding(kind="local", resolved_path=str(path), default_branch=branch)
