"""Read-only repository sight for the planning reasoner.

Reads a COMMITTED ref, never the working tree: during enrichment the tree may
hold a concurrent worker's worktree state, while the project's default branch is
the stable truth a frozen contract is written against.

Everything here is bounded and read-only by construction — `git ls-tree`,
`git show`, `git grep`. There is no write path, no command execution, and no way
to name a path outside the repository: the reasoner passes a `project_id`, and
this adapter owns the plan -> project -> repository resolution.
"""

from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath

import structlog

from praxis_orchestrator.app.ports import RepositoryOrientation, RepositorySearchHit
from praxis_orchestrator.infra.git.project_workspace import ProjectWorkspaceResolver

log = structlog.get_logger(__name__)

# Paths the planner must never see. `.git` would leak refs and object data;
# the rest are the conventional homes of credentials. A denied path reports
# "not found" rather than "denied" — the planner has no use for the difference,
# and the weaker answer cannot be used to probe for a secret's existence.
_DENIED_PREFIXES = (".git/",)
_DENIED_GLOBS = ("*.pem", "*.key", "id_rsa*", "id_ed25519*", ".env", ".env.*", "*.p12")

_TEST_DIR_NAMES = {"tests", "test", "spec", "__tests__"}
_CONFIG_FILES = (
    "pyproject.toml",
    "pytest.ini",
    "tox.ini",
    "setup.cfg",
    "package.json",
    "Makefile",
    "go.mod",
    "Cargo.toml",
)

_GIT_TIMEOUT_SECONDS = 30


class RepositoryUnavailable(RuntimeError):
    """The project's repository could not be resolved or read.

    Callers degrade — planning continues without repository tools — rather than
    failing the session: no sight is worse than sight, but it is far better than
    a crash inside a tool callback, where `execute_tool_call` would swallow the
    traceback into an opaque `{"error": ...}` the operator never sees.
    """


def _strip_leading_dot_slash(path: str) -> str:
    """Remove leading "./" SEGMENTS.

    Not `lstrip("./")`: that strips a character SET, so a dotfile like `.env`
    becomes `env` and slips past every denylist entry that names it.
    """
    while path.startswith("./"):
        path = path[2:]
    return path


def _denied(path: str) -> bool:
    normalized = _strip_leading_dot_slash(path.replace("\\", "/"))
    if any(normalized.startswith(prefix) for prefix in _DENIED_PREFIXES):
        return True
    pure = PurePosixPath(normalized)
    return any(pure.match(glob) for glob in _DENIED_GLOBS)


def _safe_relative(path: str) -> str:
    """Reject traversal and absolute paths before they reach git.

    `git show <ref>:../x` resolves against the repository root, so a traversal
    cannot escape the object database — but it can name a sibling worktree's
    content in some layouts, and there is no legitimate reason for a contract to
    reference one. Rejecting here keeps the surface obviously closed.
    """
    normalized = path.replace("\\", "/").strip()
    if not normalized:
        raise RepositoryUnavailable("empty path")
    if normalized.startswith("/") or ".." in PurePosixPath(normalized).parts:
        raise RepositoryUnavailable(f"path outside the repository: {path}")
    return _strip_leading_dot_slash(normalized)


class GitRepositoryReader:
    """`RepositoryReader` over the project's git repository."""

    def __init__(self, resolver: ProjectWorkspaceResolver) -> None:
        self._resolver = resolver

    # ---- resolution ----
    def _repo(self, project_id: str) -> tuple[Path, str]:
        """Repository root + the ref to read.

        Resolution may clone a remote project (`_materialize_remote`), so callers
        do this ONCE before a tool loop starts — never inside a tool handler,
        where an unbounded network call would be invisible.
        """
        try:
            workspace = self._resolver.resolve(project_id)
        except Exception as exc:  # noqa: BLE001 — any resolution failure degrades
            raise RepositoryUnavailable(f"cannot resolve project {project_id}: {exc}") from exc
        return workspace.repo_dir, workspace.default_branch

    def _git(self, root: Path, args: list[str]) -> str:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            raise RepositoryUnavailable(
                f"git {' '.join(args[:2])} failed: {result.stderr.strip()[:200]}"
            )
        return result.stdout

    # ---- RepositoryReader ----
    def orientation(self, project_id: str) -> RepositoryOrientation:
        root, ref = self._repo(project_id)
        top_level = [
            line
            for line in self._git(root, ["ls-tree", "--name-only", ref]).splitlines()
            if line and not _denied(line)
        ]
        tracked = self.list_paths(project_id, max_entries=2000)
        test_dirs = sorted(
            {
                path.split("/", 1)[0]
                for path in tracked
                if path.split("/", 1)[0] in _TEST_DIR_NAMES and "/" in path
            }
            | {entry for entry in top_level if entry in _TEST_DIR_NAMES}
        )
        present = tuple(name for name in _CONFIG_FILES if name in top_level)
        return RepositoryOrientation(
            default_branch=ref,
            top_level_entries=tuple(sorted(top_level)),
            test_directories=tuple(test_dirs),
            detected_test_command=_detect_test_command(present, tracked),
            config_files=present,
        )

    def list_paths(self, project_id: str, *, prefix: str = "", max_entries: int = 200) -> list[str]:
        root, ref = self._repo(project_id)
        args = ["ls-tree", "-r", "--name-only", ref]
        if prefix:
            args += ["--", _safe_relative(prefix)]
        paths = [line for line in self._git(root, args).splitlines() if line and not _denied(line)]
        return paths[:max_entries]

    def read_file(self, project_id: str, path: str, *, max_bytes: int = 20_000) -> str:
        relative = _safe_relative(path)
        if _denied(relative):
            raise RepositoryUnavailable(f"not found: {path}")
        root, ref = self._repo(project_id)
        content = self._git(root, ["show", f"{ref}:{relative}"])
        if len(content) <= max_bytes:
            return content
        # Truncate at the front: a contract is written against a file's shape,
        # and the head carries imports, signatures and class definitions.
        return content[:max_bytes] + f"\n… truncated at {max_bytes} bytes …\n"

    def search(
        self,
        project_id: str,
        pattern: str,
        *,
        path_prefix: str = "",
        max_hits: int = 50,
    ) -> list[RepositorySearchHit]:
        root, ref = self._repo(project_id)
        args = ["grep", "-n", "--fixed-strings", "-e", pattern, ref]
        if path_prefix:
            args += ["--", _safe_relative(path_prefix)]
        try:
            output = self._git(root, args)
        except RepositoryUnavailable:
            return []  # git grep exits 1 on no matches; that is an answer, not a fault
        hits: list[RepositorySearchHit] = []
        for line in output.splitlines():
            # "<ref>:<path>:<line>:<text>"
            _, _, remainder = line.partition(":")
            path, _, rest = remainder.partition(":")
            number, _, text = rest.partition(":")
            if not path or not number.isdigit() or _denied(path):
                continue
            hits.append(RepositorySearchHit(path=path, line=int(number), text=text[:300]))
            if len(hits) >= max_hits:
                break
        return hits

    def exists(self, project_id: str, path: str) -> bool:
        try:
            relative = _safe_relative(path)
        except RepositoryUnavailable:
            return False
        if _denied(relative):
            return False
        root, ref = self._repo(project_id)
        result = subprocess.run(
            ["git", "-C", str(root), "cat-file", "-e", f"{ref}:{relative}"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
        return result.returncode == 0


def _detect_test_command(config_files: tuple[str, ...], tracked: list[str]) -> str | None:
    """The command this repository conventionally verifies itself with.

    A guess, offered as orientation — the reasoner still writes the contract's
    real `verification_commands`, and the submission-time satisfiability check
    still rejects a command naming a file that does not exist.
    """
    if any(path.endswith(".py") for path in tracked) and (
        "pyproject.toml" in config_files or "pytest.ini" in config_files or "tox.ini" in config_files
    ):
        return "python -m pytest -q"
    if "package.json" in config_files:
        return "npm test"
    if "go.mod" in config_files:
        return "go test ./..."
    if "Cargo.toml" in config_files:
        return "cargo test"
    return None


__all__ = ["GitRepositoryReader", "RepositoryUnavailable"]
