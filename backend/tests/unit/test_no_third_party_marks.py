"""A third party's trademark must not re-enter the shipped tree.

`aipom` is a Pokémon — a Nintendo / Game Freak / The Pokémon Company mark. It
was this project's working name, which made it a findability problem; Phase 10B
reframed it as a legal-hygiene one, because the exposure is the presence of the
string itself in a repository about to be published.

The rename removes it. This is what stops it coming back — in a copied comment,
a restored fixture, a doc written from an old draft, or a container prefix
someone reintroduces from memory. That is not hypothetical: it had spread from
the guest hostname into the npm package name, the frontend build cache, the
e2e artefacts and the acceptance-container prefix before anyone counted.

**Deliberately NOT repository-wide.** Git history, archived run evidence
(`demos/*/runs/*`, `.orchestrator/runtime-runs/*`) and this file are out of
scope by decision, not oversight — see
`docs/superpowers/specs/2026-08-11-phase-10b-rename-scope.md`. Rewriting recorded
machine output falsifies the evidence the launch is meant to point at, and
scrubbing history means a force-push that breaks every clone. What this locks is
the tree that actually ships.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

# The marks that must not appear. Lowercased comparison, so `AIPOM` is covered.
FORBIDDEN = ("aipom",)

# What "the shipped tree" means: source, configuration, and the documents a
# reader is pointed at. Not history, not recorded evidence.
SEARCH_ROOTS = [
    REPO / "backend" / "agent_orchestrator",
    REPO / "backend" / "tests",
    REPO / "backend" / "scripts",
    REPO / "frontend" / "src",
    REPO / "frontend" / "e2e",
    REPO / "infra",
    REPO / "fixtures",
    REPO / "docs" / "architecture",
    REPO / "docs" / "decisions",
    REPO / "docs" / "guides",
]
ROOT_FILES = [
    REPO / "README.md",
    REPO / "CLAUDE.md",
    REPO / "ROADMAP.md",
    REPO / ".env.example",
    REPO / "backend" / "env.example",
    REPO / "backend" / "pyproject.toml",
    # `aipom-planner` is the npm package NAME — the mark shipped as an
    # identifier, not just as prose.
    REPO / "frontend" / "package.json",
    *sorted((REPO / ".github" / "workflows").glob("*.yml")),
]

SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".json", ".md", ".sh", ".yaml", ".yml",
    ".toml", ".css", ".html", ".cfg", ".example",
}

EXCLUDED_DIRS = {"node_modules", "__pycache__", ".venv", "dist", "static"}

# This file names the mark in order to forbid it.
EXCLUDED_FILES = {Path(__file__).resolve()}


def _candidates() -> list[Path]:
    files = [f for f in ROOT_FILES if f.exists()]
    for root in SEARCH_ROOTS:
        if not root.exists():
            continue
        for f in root.rglob("*"):
            if not f.is_file() or f.suffix not in SUFFIXES:
                continue
            if EXCLUDED_DIRS & set(f.parts):
                continue
            files.append(f)
    return sorted(set(files) - EXCLUDED_FILES)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Phase 10B has not renamed yet — the rename is blocked on trademark "
        "clearance for the replacement name, so the mark is still present in "
        "~18 shipped files. This is a RATCHET, not a suppression: strict=True "
        "means the moment the rename lands and this passes, pytest reports "
        "XPASS as a FAILURE, forcing whoever finishes the rename to delete this "
        "marker and turn the guard on permanently. Do not remove the marker "
        "before the tree is clean; do not leave it after."
    ),
)
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
