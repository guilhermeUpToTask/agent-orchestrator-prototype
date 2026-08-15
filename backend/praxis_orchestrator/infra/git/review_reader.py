"""Read-only diffs for the per-goal review surface.

Why this exists at all: a cycle branch is ONE large diff, and review research
puts defect detection near 87% under 100 changed lines and near 28% over 1,000.
The orchestrator is the only component that can split it usefully, because it
recorded the internal boundaries — which task produced which commit, which
commit was test-authoring and which was implementation, and what the protected
scope was. Nothing else in the pipeline knows that.

Deliberately read-only and deliberately NOT hunk-level accept/reject: half
accepting a candidate invalidates the revision-bound evidence that makes it
trustworthy, so acceptance stays at the granularity the orchestrator can
actually verify. This module can only look.

Separate from `repository_reader.py`, which serves the PLANNER bounded sight of
a committed ref during a reasoning session. Different consumer, different
bounds, different failure behaviour: a planner tool degrades to "I could not
read that", while a review surface must say plainly that the diff is missing.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

_GIT_TIMEOUT_SECONDS = 20
# A patch this big has stopped being reviewable prose and started being a
# download. The endpoint reports truncation rather than pretending.
DEFAULT_MAX_PATCH_BYTES = 200_000

# The bands come from the review research the roadmap cites. Serving the band
# rather than only the number is what makes that research actionable at the
# moment somebody decides how carefully to look.
_SMALL_MAX = 100
_MODERATE_MAX = 400
_LARGE_MAX = 1_000


class ReviewDiffUnavailable(RuntimeError):
    """The repository could not answer. Never silently an empty diff — an empty
    diff and an unreadable repository mean opposite things to a reviewer."""


@dataclass(frozen=True)
class FileChange:
    path: str
    insertions: int
    deletions: int
    # git reports "-" for binary files rather than a count.
    binary: bool = False


@dataclass(frozen=True)
class DiffStat:
    files_changed: int
    insertions: int
    deletions: int
    files: list[FileChange] = field(default_factory=list)

    @property
    def changed_lines(self) -> int:
        return self.insertions + self.deletions

    @property
    def review_band(self) -> str:
        """How carefully this wants to be read: `small | moderate | large | very_large`."""
        total = self.changed_lines
        if total <= _SMALL_MAX:
            return "small"
        if total <= _MODERATE_MAX:
            return "moderate"
        if total <= _LARGE_MAX:
            return "large"
        return "very_large"


@dataclass(frozen=True)
class Patch:
    text: str
    truncated: bool
    total_bytes: int


def _git(repo: Path, args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReviewDiffUnavailable(f"git {args[0]} timed out in {repo}") from exc
    if result.returncode != 0:
        raise ReviewDiffUnavailable(
            f"git {' '.join(args[:2])} failed: {result.stderr.strip()[:200]}"
        )
    return result.stdout


def _parse_numstat(raw: str) -> DiffStat:
    files: list[FileChange] = []
    insertions = 0
    deletions = 0
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added, removed, path = parts[0], parts[1], parts[-1]
        if added == "-" or removed == "-":
            files.append(FileChange(path=path, insertions=0, deletions=0, binary=True))
            continue
        add_n, del_n = int(added), int(removed)
        insertions += add_n
        deletions += del_n
        files.append(FileChange(path=path, insertions=add_n, deletions=del_n))
    return DiffStat(
        files_changed=len(files),
        insertions=insertions,
        deletions=deletions,
        files=files,
    )


class GitReviewReader:
    """Diffs between two commits already recorded as evidence.

    Every method takes explicit SHAs. It never derives a range from a branch
    name: branches move, and a review surface that silently re-anchors after a
    later merge would show a different change than the evidence describes.
    """

    def diff_stat(self, repo: Path, base: str, head: str) -> DiffStat:
        return _parse_numstat(_git(repo, ["diff", "--numstat", f"{base}..{head}"]))

    def commit_stat(self, repo: Path, sha: str) -> DiffStat:
        """One commit against its first parent — what that commit introduced."""
        return _parse_numstat(
            _git(repo, ["diff", "--numstat", f"{sha}^!", "--"])
        )

    def merge_stat(self, repo: Path, merge_sha: str) -> DiffStat:
        """What a goal's merge commit brought onto the cycle branch.

        `<sha>^1..<sha>` — first parent is the cycle branch as it was, second is
        the goal branch, so this is exactly the goal's contribution and not the
        work siblings had already merged.
        """
        return _parse_numstat(
            _git(repo, ["diff", "--numstat", f"{merge_sha}^1..{merge_sha}"])
        )

    def patch(
        self,
        repo: Path,
        base: str,
        head: str,
        *,
        max_bytes: int = DEFAULT_MAX_PATCH_BYTES,
    ) -> Patch:
        raw = _git(repo, ["diff", f"{base}..{head}"])
        encoded = raw.encode("utf-8", errors="replace")
        if len(encoded) <= max_bytes:
            return Patch(text=raw, truncated=False, total_bytes=len(encoded))
        clipped = encoded[:max_bytes].decode("utf-8", errors="ignore")
        return Patch(text=clipped, truncated=True, total_bytes=len(encoded))

    def resolves(self, repo: Path, sha: str) -> bool:
        """Whether a recorded SHA still exists in this repository.

        The evidence document promises its SHAs resolve; a review surface that
        404s on a garbage-collected commit should say which one is missing
        rather than fail wholesale.
        """
        try:
            _git(repo, ["cat-file", "-e", f"{sha}^{{commit}}"])
        except ReviewDiffUnavailable:
            return False
        return True
