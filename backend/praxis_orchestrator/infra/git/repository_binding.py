"""What a project's `repo_url` must be for a plan to run against it.

Write-time validation, called by the projects router exactly as
`routers/reasoner.py` calls `validate_reasoner_config`. Remote URLs are checked
for syntax only: a create request must not block on a slow or unreachable host,
and a repository reachable now may not be at execution time, so a network probe
would cost a timeout and buy very little.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlparse

from praxis_orchestrator.infra.errors import ProjectBindingInvalidError

BindingKind = Literal["local", "remote", "scratch"]

# `git@github.com:acme/widgets.git` — the form every forge prints as "the SSH
# URL". It has no URL scheme (`@` and `.` are not legal scheme characters), so
# `urlparse` leaves `scheme` empty and it would otherwise fall through to the
# local-path branch and be refused as a missing directory: a message naming the
# wrong cause, for the most common remote form there is.
#
# It is genuinely unsupported rather than merely unvalidated —
# `ProjectWorkspaceResolver.repository_path_for` makes the same scheme-based
# assumption and `_materialize_remote` skips a scheme-less URL, so no clone was
# ever attempted — so this refuses it by name and points at the two forms that
# do work.
#
# Anchored to reject a leading `/`, and the host part excludes `/`, so a real
# directory called `user@corp/repo` is still read as a path.
_SCP_STYLE = re.compile(r"^[^/@]+@[^/:]+:")

# git will happily block forever asking a human for a username. Nothing in the
# orchestrator runs on a tty — a worker holding a goal lease would simply stop,
# with no error, no timeout and no way to tell it apart from slow work — so
# every git subprocess that can reach a remote runs with prompting disabled and
# ssh in batch mode. Failing fast turns a hang into a classified error.
GIT_NONINTERACTIVE_ENV: dict[str, str] = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "echo",
    "SSH_ASKPASS": "echo",
    "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new",
}


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


def default_branch_of(repo: Path) -> str | None:
    """The branch plan work is cut from, probed on disk.

    Public because `validate_repo_url` cannot answer this for a REMOTE binding —
    it never touches the filesystem there, by design (see the module docstring).
    The cycle evidence read model still has to name the branch an operator
    should diff against, and by then the clone exists, so it probes the resolved
    path rather than re-deriving these two `symbolic-ref` calls a second time.
    """
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


@dataclass(frozen=True)
class RemoteProbe:
    """What one `git ls-remote` says about a remote, classified.

    `problem_kind` exists because the operator's next step differs per kind:
    needing credentials is a token problem, `not_found` is a typo, and nobody
    should have to read git's stderr to tell them apart.
    """

    reachable: bool
    default_branch: str | None = None
    problem: str | None = None
    problem_kind: str | None = None  # needs_credentials | not_found | unreachable | timeout


def _classify_ls_remote_failure(stderr: str) -> str:
    lowered = stderr.lower()
    if (
        "authentication" in lowered
        or "could not read username" in lowered
        or "could not read password" in lowered
        or "permission denied" in lowered
        or "access denied" in lowered
    ):
        return "needs_credentials"
    if "not found" in lowered or "does not exist" in lowered:
        return "not_found"
    return "unreachable"


def probe_remote(repo_url: str, timeout_seconds: float = 5.0) -> RemoteProbe:
    """Ask a remote whether it exists, without downloading it.

    Deliberately NOT called from a write path — see the module docstring, which
    records why create must stay network-free. This is the setup-time check,
    made at a moment a human is watching, and it must never block: the
    non-interactive environment plus a hard timeout.
    """
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--symref", "--", repo_url, "HEAD"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env={**os.environ, **GIT_NONINTERACTIVE_ENV},
        )
    except subprocess.TimeoutExpired:
        return RemoteProbe(
            reachable=False,
            problem=f"{repo_url} did not answer within {timeout_seconds:.0f}s",
            problem_kind="timeout",
        )

    if result.returncode != 0:
        stderr = result.stderr.strip()
        return RemoteProbe(
            reachable=False,
            problem=stderr or f"git ls-remote exited {result.returncode}",
            problem_kind=_classify_ls_remote_failure(stderr),
        )

    default_branch: str | None = None
    for line in result.stdout.splitlines():
        if line.startswith("ref:"):
            # "ref: refs/heads/trunk\tHEAD"
            default_branch = line.split()[1].removeprefix("refs/heads/")
            break
    return RemoteProbe(reachable=True, default_branch=default_branch)


def validate_repo_url(repo_url: str | None) -> RepositoryBinding:
    if not repo_url or not repo_url.strip():
        return RepositoryBinding(kind="scratch", resolved_path=None, default_branch=None)

    candidate = repo_url.strip()
    if urlparse(candidate).scheme == "" and _SCP_STYLE.match(candidate):
        raise ProjectBindingInvalidError(
            f"scp-style git remotes are not supported ({candidate}); "
            "use the ssh:// form (ssh://git@host/owner/repo.git) or https://"
        )

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
    branch = default_branch_of(path)
    if branch is None:
        raise ProjectBindingInvalidError(
            f"cannot determine the default branch of {path}; it has no HEAD and no branches"
        )
    return RepositoryBinding(kind="local", resolved_path=str(path), default_branch=branch)
