"""A third party's trademark must not re-enter the shipped tree.

The strings in `FORBIDDEN` below are marks belonging to other people. One of
them was this project's working name, which made it a findability problem;
Phase 10B reframed it as a legal-hygiene one, because the exposure is the
presence of the string itself in a repository about to be published.

The rename removes it. This is what stops it coming back — in a copied comment,
a restored fixture, a doc written from an old draft, or a container prefix
someone reintroduces from memory. That is not hypothetical: it had spread from
the guest hostname into the npm package name, the frontend build cache, the
e2e artefacts and the acceptance-container prefix before anyone counted.

**Git history is deliberately out of scope**, by decision on 2026-08-11: the
mark may remain in history, but not in the working tree. Scrubbing history means
`git filter-repo` and a force-push, which breaks every clone and rewrites the
audit trail this project's discipline rests on — a disproportionate remedy for a
string in the commit log of a never-published project.

Only this file is excluded from the scan, because it has to name the mark in
order to forbid it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

# The marks that must not appear. Lowercased comparison, so `AIPOM` is covered.
FORBIDDEN = ("aipom",)

# The WHOLE working tree, not a curated subset. The mark had spread from the
# guest hostname into an npm package name, a container prefix, a build-cache
# directory and e2e artefacts before anyone counted, so a scan that only looked
# where it was expected would have missed most of it.
EXCLUDED_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "static",  # the built UI staged into the wheel; git-ignored
}

# Text formats only — a binary match would be a false positive we cannot read.
SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".sh", ".bash",
    ".yaml", ".yml", ".toml", ".cfg", ".ini", ".css", ".html", ".txt",
    ".example", ".env", ".out", ".log", ".sql", ".xml",
}

# This file names the mark in order to forbid it.
EXCLUDED_FILES = {Path(__file__).resolve()}


def _candidates() -> list[Path]:
    files: list[Path] = []
    for f in REPO.rglob("*"):
        if not f.is_file():
            continue
        if EXCLUDED_DIRS & set(f.parts):
            continue
        # Extensionless files at the root (LICENSE, Makefile) are worth reading.
        if f.suffix not in SUFFIXES and f.suffix != "":
            continue
        if f.suffix == "" and f.parent != REPO and f.parent.name != "dev-vm":
            continue
        files.append(f)
    return sorted(set(files) - EXCLUDED_FILES)


@pytest.mark.parametrize("mark", FORBIDDEN)
def test_no_third_party_mark_in_the_shipped_tree(mark: str) -> None:
    offenders: list[str] = []
    for path in _candidates():
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        if mark in text:
            line = next(
                (
                    n
                    for n, raw in enumerate(text.splitlines(), 1)
                    if mark in raw
                ),
                0,
            )
            offenders.append(f"{path.relative_to(REPO)}:{line}")

    assert offenders == [], (
        f"'{mark}' is a third party's trademark and must not appear in the "
        f"shipped tree. Found in: {offenders}"
    )


def test_the_scan_actually_reads_files(tmp_path: Path) -> None:
    """A guard whose file list silently went empty would pass forever."""
    assert len(_candidates()) > 100, "the search roots resolved to almost nothing"
    assert any(p.name == "README.md" for p in _candidates())
