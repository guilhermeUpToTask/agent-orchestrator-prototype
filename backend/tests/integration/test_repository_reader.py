"""GitRepositoryReader against a real git repository.

The planner used to write contracts blind — `read_repository_context` returned a
hardcoded `{"availability": "adapter_context_only"}` — and invented
`pytest -q tests/test_greet.py` for a repository whose file is
`tests/test_greeter.py`. These tests pin the sight that replaces the guess, and
the boundaries that keep it safe to hand to a model.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.domain.entities.project_definition import ProjectDefinition
from src.infra.git.project_workspace import ProjectWorkspaceResolver
from src.infra.git.repository_reader import GitRepositoryReader, RepositoryUnavailable

pytestmark = pytest.mark.integration

PROJECT_ID = "project-1"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


class _Projects:
    def __init__(self, project: ProjectDefinition) -> None:
        self._project = project

    def get(self, project_id: str) -> ProjectDefinition:
        if project_id != self._project.id:
            raise KeyError(project_id)
        return self._project

    def list(self) -> list[ProjectDefinition]:
        return [self._project]


@pytest.fixture
def reader(tmp_path) -> GitRepositoryReader:
    repo = tmp_path / "seed-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.email", "t@example.test")
    _git(repo, "config", "user.name", "t")

    (repo / "pyproject.toml").write_text("[project]\nname='happy'\n")
    (repo / "src" / "happy_path").mkdir(parents=True)
    (repo / "src" / "happy_path" / "greeter.py").write_text(
        "def greet(name: str) -> str:\n    raise NotImplementedError\n"
    )
    (repo / "tests").mkdir()
    (repo / "tests" / "test_greeter.py").write_text(
        "from happy_path.greeter import greet\n\n\ndef test_greet():\n    assert greet('Ada')\n"
    )
    (repo / ".env").write_text("OPENROUTER_API_KEY=sk-real-secret\n")
    (repo / "deploy.pem").write_text("-----BEGIN PRIVATE KEY-----\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "seed")

    # The reader must serve the COMMITTED ref, so leave dirty working-tree state
    # behind to prove it is not what gets read.
    (repo / "src" / "happy_path" / "greeter.py").write_text("WORKTREE SCRATCH\n")

    resolver = ProjectWorkspaceResolver(
        _Projects(ProjectDefinition(id=PROJECT_ID, name="happy", repo_url=str(repo))),
        tmp_path / "home",
    )
    return GitRepositoryReader(resolver)


def test_orientation_answers_what_am_i_planning_against(reader) -> None:
    orientation = reader.orientation(PROJECT_ID)

    assert orientation.default_branch == "main"
    assert "src" in orientation.top_level_entries and "tests" in orientation.top_level_entries
    assert orientation.test_directories == ("tests",)
    assert orientation.config_files == ("pyproject.toml",)
    # the one fact that would have prevented the live failure
    assert orientation.detected_test_command == "python -m pytest -q"


def test_list_paths_reveals_the_real_test_filename(reader) -> None:
    paths = reader.list_paths(PROJECT_ID, prefix="tests")

    assert paths == ["tests/test_greeter.py"]  # not tests/test_greet.py


def test_reads_the_committed_ref_not_the_dirty_working_tree(reader) -> None:
    """During enrichment the working tree may hold a concurrent worker's
    worktree state; the default branch is the truth a contract is written
    against."""
    content = reader.read_file(PROJECT_ID, "src/happy_path/greeter.py")

    assert "def greet" in content
    assert "WORKTREE SCRATCH" not in content


def test_search_locates_a_symbol_with_line_numbers(reader) -> None:
    hits = reader.search(PROJECT_ID, "def greet")

    assert [(hit.path, hit.line) for hit in hits] == [("src/happy_path/greeter.py", 1)]


def test_search_with_no_match_is_an_answer_not_a_failure(reader) -> None:
    """`git grep` exits 1 on no matches. Surfacing that as an error would teach
    the model that repository inspection is broken — the exact licence to guess
    that this whole adapter exists to remove."""
    assert reader.search(PROJECT_ID, "def nonexistent_symbol") == []


def test_exists_is_the_satisfiability_primitive(reader) -> None:
    assert reader.exists(PROJECT_ID, "tests/test_greeter.py") is True
    assert reader.exists(PROJECT_ID, "tests/test_greet.py") is False


@pytest.mark.parametrize("secret", [".env", "deploy.pem"])
def test_secrets_are_invisible_even_though_they_are_tracked(reader, secret: str) -> None:
    assert secret not in reader.list_paths(PROJECT_ID, max_entries=500)
    assert reader.exists(PROJECT_ID, secret) is False
    with pytest.raises(RepositoryUnavailable, match="not found"):
        reader.read_file(PROJECT_ID, secret)


@pytest.mark.parametrize("path", ["../outside.txt", "/etc/passwd", "src/../../escape.py"])
def test_traversal_and_absolute_paths_are_refused(reader, path: str) -> None:
    with pytest.raises(RepositoryUnavailable, match="outside the repository|empty path"):
        reader.read_file(PROJECT_ID, path)
    assert reader.exists(PROJECT_ID, path) is False


def test_a_large_file_is_truncated_rather_than_costing_a_terminal_token_limit(
    reader, tmp_path
) -> None:
    """An unbounded read costs TOKEN_LIMIT, which is non-retryable — a careless
    call would be strictly worse than having no repository sight at all."""
    content = reader.read_file(PROJECT_ID, "tests/test_greeter.py", max_bytes=20)

    assert len(content) < 200
    assert "truncated" in content


def test_an_unresolvable_project_degrades_with_a_typed_error(reader) -> None:
    with pytest.raises(RepositoryUnavailable, match="cannot resolve project"):
        reader.orientation("no-such-project")
