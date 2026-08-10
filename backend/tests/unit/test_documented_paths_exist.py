"""A documented path that does not exist is a bug in the doc — fail the build.

The repo already asserts "a doc contradicting the code is a bug in the doc, fix
it in the same PR" (CLAUDE.md). Review is not what enforces that: the P8.7 audit
found CLAUDE.md's own repository tree naming `backend/src/domain/`,
`backend/src/app/` and `backend/src/infra/` — three directories that have never
existed under those names, in the one file whose header claims it OVERRIDES
default behaviour. Every session that trusted it followed a map to nowhere.

So the mechanically-checkable subset is checked mechanically. This is the same
argument `test_capability_matrix.py` makes for routes and
`test_fixture_docs_contract.py` makes for the walkthroughs, applied to paths.

Scope is deliberately narrow — a path is only checked when the doc committed to
it being a real file: it is inside backticks, it contains a `/`, and it starts
with a real top-level directory of this repository. Prose, URLs, globs, route
templates and code identifiers are all excluded, because a check that cries wolf
gets deleted rather than fixed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

# The docs whose paths are load-bearing: the instruction file every session
# reads, and the architecture set it points at.
DOCUMENTS = [REPO / "CLAUDE.md", *sorted((REPO / "docs" / "architecture").glob("*.md"))]

# A path claim only counts when it is rooted in a real top-level directory. The
# repository has no single-word top-level source dir, so this also excludes bare
# module names and `a/b` prose fragments.
#
# `infra/` is deliberately absent even though the repository has one: the docs
# use that prefix for the BACKEND package (`backend/agent_orchestrator/infra/`),
# while the top-level `infra/` is the dev-VM. Checking it here would report a
# real ambiguity as a missing file over and over; the honest fix for those
# references is to write them out in full, which this test then checks.
_ROOTS = ("backend/", "docs/", "frontend/", "fixtures/", ".github/")

# What a real file claim ends in. Anything else with a dot in its last segment
# is a `module.function` reference, not a path.
_SUFFIXES = (".py", ".md", ".sh", ".ts", ".tsx", ".yaml", ".yml", ".toml", ".json", ".sql")

# Build outputs: real paths that exist only after a build step, and are
# git-ignored by design. Naming them in a doc is correct; asserting they are
# present in a fresh checkout is not.
_GENERATED = {"backend/agent_orchestrator/api/static"}

_BACKTICKED = re.compile(r"`([^`\n]+)`")

# Trailing punctuation a sentence leaves attached to a path inside backticks.
_TRAILING = ".,;:"


def _claims(document: Path) -> set[str]:
    found: set[str] = set()
    for raw in _BACKTICKED.findall(document.read_text()):
        candidate = raw.strip().rstrip(_TRAILING)
        if not candidate.startswith(_ROOTS):
            continue
        if any(ch in candidate for ch in " *{}<>()|"):
            continue  # prose, a glob, a route template, or a signature
        if "::" in candidate:
            continue  # a pytest node id
        candidate = candidate.rstrip("/")  # a directory written with a trailing slash
        tail = candidate.rsplit("/", 1)[-1]
        if "." in tail and not candidate.endswith(_SUFFIXES):
            continue  # `module.function`, not a file
        found.add(candidate)
    return found


@pytest.mark.parametrize("document", DOCUMENTS, ids=lambda path: path.name)
def test_every_documented_path_exists(document: Path) -> None:
    missing = sorted(
        claim
        for claim in _claims(document) - _GENERATED
        if not (REPO / claim).exists()
    )

    assert missing == [], (
        f"{document.relative_to(REPO)} names paths that do not exist. Fix the doc "
        f"in the same change that moved them: {missing}"
    )


def test_the_extractor_catches_the_defect_it_exists_for(tmp_path: Path) -> None:
    """The historical failure, against a synthetic document.

    `backend/src/domain/` is what CLAUDE.md claimed for months. Without this,
    an extractor that quietly stopped recognising path claims would make every
    assertion above pass while checking nothing — the exact failure mode of a
    contract test.
    """
    document = tmp_path / "sample.md"
    document.write_text(
        "The package lives at `backend/src/domain/` and the loop at\n"
        "`backend/agent_orchestrator/app/use_cases/run_worker.py`.\n"
        "Resolution happens in `infra/forge/binding.read_binding`, and the\n"
        "route is `POST /api/plans/{plan_id}/pause`.\n"
    )

    claims = _claims(document)

    assert "backend/src/domain" in claims, "the wrong claim must still be seen"
    assert "backend/agent_orchestrator/app/use_cases/run_worker.py" in claims
    assert not any("read_binding" in claim for claim in claims), "module.function is not a path"
    assert not any("{plan_id}" in claim for claim in claims), "a route template is not a path"

    missing = sorted(claim for claim in claims if not (REPO / claim).exists())
    assert missing == ["backend/src/domain"]
