"""The operator walkthrough must not teach a binding that does not work.

Was a known issue: the fixture told operators to `export PROJECT_REPO_DIR`, but
`AppContainer` does not read it. Repository routing is project-scoped through
`ProjectDefinition.repo_url`, and a project WITHOUT one gets a fresh empty repo
auto-seeded at `$ORCHESTRATOR_HOME/projects/<id>/repo` — so a live run edits a
tree the checker never inspects and "passes" against nothing.

Found on the first real run of the fixture. Locked here because the defect lived
in the DOCS: nothing in the backend suite could have caught it, and the next
person to write a walkthrough will reach for the same variable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
BACKEND_SRC = Path(__file__).resolve().parents[2] / "src"


def _walkthrough_docs() -> list[Path]:
    return sorted(
        path
        for path in FIXTURES.rglob("*")
        if path.suffix in {".md", ".sh"} and path.is_file()
    )


def test_the_composition_root_really_does_not_read_the_variable() -> None:
    """The premise. If this ever changes, the guidance below can change with it."""
    container = (BACKEND_SRC / "infra" / "container.py").read_text()
    readers = [
        line
        for line in container.splitlines()
        if "PROJECT_REPO_DIR" in line and "environ" in line
    ]

    assert readers == [], f"container now reads PROJECT_REPO_DIR: {readers}"


@pytest.mark.parametrize("doc", _walkthrough_docs(), ids=lambda p: p.name)
def test_no_fixture_presents_project_repo_dir_as_the_binding(doc: Path) -> None:
    """Mentioning it to WARN is fine — that is what the README now does. Telling
    an operator to export it as the way to bind a repository is not."""
    text = doc.read_text()
    if "PROJECT_REPO_DIR" not in text:
        return

    for number, line in enumerate(text.splitlines(), start=1):
        if "PROJECT_REPO_DIR" not in line or line.lstrip().startswith("#"):
            continue
        assert not line.lstrip().startswith("export PROJECT_REPO_DIR"), (
            f"{doc.name}:{number} tells the operator to bind the repository with "
            "PROJECT_REPO_DIR, which the container does not read; the project's "
            "repo_url is the binding"
        )


def test_the_happy_path_readme_states_the_real_binding() -> None:
    readme = (FIXTURES / "happy-path-v1" / "README.md").read_text()

    assert "repo_url" in readme
    assert "PROJECT_REPO_DIR" in readme  # named, so the trap is recognisable
