"""Harness contracts for the `static-site-v1` demo.

A demo RUN is never locked in CI — a real reasoner decomposes differently every
time, so asserting the outcome would fail a system that is working. But the
demo's own files are ordinary repository contents, and three properties of them
must hold or the demo proves nothing:

  1. the seed does not already contain the answer;
  2. the acceptance check CANNOT pass against that seed, so a green result
     means something;
  3. the brief is postable verbatim.

Locking these is not the same as locking the run. `happy-path-v1` had to learn
(2) the expensive way: its verdict was circular because it ran pytest inside
the same `tests/` the agent writes to, so the checker could be satisfied by a
weak test rather than by working code.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO = REPO_ROOT / "demos" / "static-site-v1"


def test_the_demo_exists_where_the_docs_say_it_does():
    assert (DEMO / "README.md").exists()
    assert (DEMO / "brief.txt").exists()
    assert (DEMO / "seed").is_dir()
    assert (DEMO / "acceptance" / "test_acceptance.py").exists()
    assert (DEMO / "scripts" / "verify_demo.py").exists()
    assert (DEMO / "scripts" / "materialize.sh").exists()


def test_the_seed_does_not_contain_the_answer():
    """The whole demo is worthless if a solution is committed by accident."""
    sources = list((DEMO / "seed" / "src").rglob("*.py"))
    assert sources, "the seed should still ship a package"

    body = "\n".join(path.read_text(encoding="utf-8") for path in sources)
    # No renderer, no CLI, no front-matter parser — the four things the brief asks for.
    for forbidden in ("def render", "def build", "front_matter", "argparse", "<h1>"):
        assert forbidden not in body, f"the seed already implements {forbidden!r}"

    assert not (DEMO / "seed" / "src" / "sitegen" / "cli.py").exists()


def test_the_seed_ships_the_content_the_tool_must_handle():
    """The acceptance check renders these exact files, so they are part of the
    contract rather than sample data."""
    index = (DEMO / "seed" / "content" / "index.md").read_text(encoding="utf-8")

    assert index.startswith("---"), "front matter is what task 1 has to parse"
    assert "title: Welcome" in index
    assert "[links to other pages](about.md)" in index, "the link-rewriting case"
    assert "**strong**" in index and "*emphasis*" in index
    assert (DEMO / "seed" / "content" / "about.md").exists()


def test_the_seed_starts_with_no_tests_of_its_own():
    """Every task must author its own checks. A pre-existing test suite would
    hand the agent a baseline it did not earn."""
    tests = [p for p in (DEMO / "seed" / "tests").iterdir() if p.name != ".gitkeep"]
    assert tests == [], f"the seed tests/ directory must be empty, found {tests}"


def test_the_brief_is_postable_verbatim():
    """It is sent to the reasoner as-is. Prose about the demo belongs in the
    README — `happy-path-v1` shipped a BRIEF.md whose commentary would have
    travelled to the model as part of the brief."""
    brief = (DEMO / "brief.txt").read_text(encoding="utf-8")

    assert brief.strip()
    assert not brief.lstrip().startswith("#"), "a markdown title is documentation, not a brief"
    # The constraints that keep the run honest and comparable.
    assert "python -m pytest -q" in brief
    assert "standard library" in brief.lower()
    assert "tests" in brief.lower()


def test_the_acceptance_check_cannot_pass_against_the_seed(tmp_path):
    """The discrimination property. If this suite passed on an unimplemented
    tool, a green run afterwards would prove nothing at all."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["cp", "-a", f"{DEMO / 'seed'}/.", str(repo)], check=True, capture_output=True
    )

    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(DEMO / "acceptance"), "-q"],
        capture_output=True,
        text=True,
        timeout=180,
        env={"SITEGEN_REPO": str(repo), "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
    )

    assert result.returncode != 0, (
        "the acceptance check passed against a seed with no implementation, so "
        "it discriminates nothing:\n" + result.stdout[-2000:]
    )


def test_the_acceptance_check_skips_rather_than_fails_when_unpointed():
    """Exit codes are read by an operator. A suite that ERRORS because nobody
    set an env var reads as a broken product."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(DEMO / "acceptance"), "-q"],
        capture_output=True,
        text=True,
        timeout=120,
        env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"},
    )

    assert result.returncode == 0, result.stdout[-1500:]
    assert "skipped" in result.stdout.lower()


def test_the_structural_checker_documents_its_exit_codes():
    """0 / 1 / 2 are load-bearing: conflating "a check failed" with "the
    harness is broken" publishes a broken harness as a defect, or dismisses a
    defect as a broken harness."""
    source = (DEMO / "scripts" / "verify_demo.py").read_text(encoding="utf-8")

    assert "every check passed" in source
    assert "a check FAILED" in source
    assert "harness is broken" in source
    assert "sys.exit(2)" in source


def test_the_structural_checker_asserts_no_goal_count():
    """The one thing a demo must never assert: a real reasoner decomposes
    differently every run, so a pinned count fails a working system."""
    source = (DEMO / "scripts" / "verify_demo.py").read_text(encoding="utf-8")

    assert "decomposes differently" in source
    assert "len(goals) ==" not in source


def test_the_demos_readme_separates_demos_from_fixtures():
    """The category error this directory exists to prevent."""
    readme = (REPO_ROOT / "demos" / "README.md").read_text(encoding="utf-8")

    assert "Demos are not fixtures" in readme
    assert "catch regressions" in readme
    assert "A red run is published, not retried" in readme
