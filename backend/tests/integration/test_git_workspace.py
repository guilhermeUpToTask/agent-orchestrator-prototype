"""GitBranchWorkspace on a real git repo: commit lands on the plan branch,
discard is a true rollback, retries begin clean (stateless task execution)."""

from __future__ import annotations

import asyncio
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.app.testing.fakes import FakeClock
from src.domain.entities.project_definition import ProjectDefinition
from src.infra.git.project_workspace import ProjectWorkspaceResolver
from src.infra.git.workspace import GitBranchWorkspace, LocalDirWorkspace
from src.infra.runtime.verification_executor import LocalVerificationExecutor

pytestmark = pytest.mark.integration


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _init_repo(repo: Path, branch: str) -> None:
    subprocess.run(
        ["git", "init", "-b", branch, str(repo)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(
        repo,
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.test",
        "commit",
        "--allow-empty",
        "-m",
        "initial",
    )


class MutableProjects:
    def __init__(self, project: ProjectDefinition) -> None:
        self.project = project

    def get(self, project_id: str) -> ProjectDefinition:
        assert project_id == self.project.id
        return self.project

    def list(self) -> list[ProjectDefinition]:
        return [self.project]


@pytest.fixture
def repo(tmp_path):
    return tmp_path / "project-repo"


def test_commit_merges_into_plan_branch(repo):
    ws = GitBranchWorkspace(repo)

    async def flow():
        handle = await ws.begin("p1", "t1", 1)
        (Path(handle.path) / "feature.py").write_text("print('hi')\n")
        await ws.commit(handle)
        return handle

    handle = asyncio.run(flow())
    files = _git(repo, "ls-tree", "-r", "--name-only", "plan/p1")
    assert "feature.py" in files.splitlines()
    # merge is --no-ff: the task branch history is visible as a merge commit
    assert "merge: task/t1/a1" in _git(repo, "log", "--oneline", "plan/p1")
    # the task worktree is cleaned up
    assert not Path(handle.path).exists()


def test_discard_is_a_true_rollback(repo):
    ws = GitBranchWorkspace(repo)

    async def flow():
        handle = await ws.begin("p1", "t1", 1)
        (Path(handle.path) / "half-baked.py").write_text("broken\n")
        await ws.discard(handle)
        return handle

    handle = asyncio.run(flow())
    files = _git(repo, "ls-tree", "-r", "--name-only", "plan/p1")
    assert "half-baked.py" not in files
    # task branch deleted, worktree gone — as if the attempt never happened
    branches = _git(repo, "branch", "--list", "task/t1/*")
    assert branches == ""
    assert not Path(handle.path).exists()


def test_retry_attempt_begins_clean_from_plan_branch(repo):
    """Stateless task exec: attempt 2 must not see attempt 1's discarded mess,
    but must see prior COMMITTED work on the plan branch."""
    ws = GitBranchWorkspace(repo)

    async def flow():
        committed = await ws.begin("p1", "t0", 1)
        (Path(committed.path) / "done.py").write_text("ok\n")
        await ws.commit(committed)

        failed = await ws.begin("p1", "t1", 1)
        (Path(failed.path) / "garbage.py").write_text("broken\n")
        await ws.discard(failed)

        retry = await ws.begin("p1", "t1", 2)
        contents = {p.name for p in Path(retry.path).iterdir() if p.name != ".git"}
        await ws.discard(retry)
        return contents

    contents = asyncio.run(flow())
    assert "done.py" in contents  # committed history visible
    assert "garbage.py" not in contents  # discarded attempt invisible


def test_main_branch_untouched_by_plan_work(repo):
    ws = GitBranchWorkspace(repo)

    async def flow():
        handle = await ws.begin("p1", "t1", 1)
        (Path(handle.path) / "feature.py").write_text("x\n")
        await ws.commit(handle)

    asyncio.run(flow())
    assert "feature.py" not in _git(repo, "ls-tree", "-r", "--name-only", "main")


def test_local_dir_workspace_hands_out_the_dir(tmp_path):
    ws = LocalDirWorkspace(tmp_path / "out")

    async def flow():
        handle = await ws.begin("p1", "t1", 1)
        return handle.path

    path = asyncio.run(flow())
    assert Path(path) == tmp_path / "out"
    assert Path(path).is_dir()


def test_cycle_task_commit_stops_at_goal_branch(repo):
    ws = GitBranchWorkspace(repo)

    async def flow():
        handle = await ws.begin(
            "p1",
            "t1",
            1,
            cycle_id="c1",
            goal_id="g1",
            run_id="run-123",
        )
        (Path(handle.path) / "verified.py").write_text("ok\n")
        await ws.commit(handle)

    asyncio.run(flow())

    assert "verified.py" in _git(repo, "ls-tree", "-r", "--name-only", "goal/g1").splitlines()
    assert "verified.py" not in _git(repo, "ls-tree", "-r", "--name-only", "cycle/c1").splitlines()
    assert _git(repo, "branch", "--list", "task/t1/run-123") == ""
    assert "merge: task/t1/run-123 into goal/g1" in _git(repo, "log", "--oneline", "goal/g1")


def test_changed_paths_include_agent_commits_since_task_base(repo):
    ws = GitBranchWorkspace(repo)
    verifier = LocalVerificationExecutor(FakeClock(datetime(2026, 7, 14, tzinfo=timezone.utc)))

    async def flow():
        handle = await ws.begin("p1", "t1", 1)
        root = Path(handle.path)
        (root / "committed.py").write_text("committed\n")
        _git(root, "add", "committed.py")
        _git(
            root,
            "-c",
            "user.name=agent",
            "-c",
            "user.email=agent@example.test",
            "commit",
            "-m",
            "agent commit",
        )
        (root / "dirty.py").write_text("dirty\n")
        paths = await verifier.changed_paths(handle.path, handle.base_ref)
        await ws.discard(handle)
        return paths

    assert asyncio.run(flow()) == ["committed.py", "dirty.py"]


def test_workspace_prune_and_audit_keep_live_refs_visible(repo):
    ws = GitBranchWorkspace(repo)

    async def flow():
        handle = await ws.begin("p1", "t1", 1)
        before = await ws.audit()
        await ws.discard(handle)
        await ws.prune()
        after = await ws.audit()
        return before, after, handle

    before, after, handle = asyncio.run(flow())
    assert "plan/p1" in before["branches"]
    assert "task/t1/a1" in before["branches"]
    assert any(handle.path in row for row in before["worktrees"])
    assert "plan/p1" in after["branches"]
    assert "task/t1/a1" not in after["branches"]
    assert not any(handle.path in row for row in after["worktrees"])


def test_project_resolver_uses_the_repository_default_branch(tmp_path):
    repo = tmp_path / "trunk-repo"
    _init_repo(repo, "trunk")
    projects = MutableProjects(
        ProjectDefinition(id="project-1", name="Project", repo_url=str(repo))
    )
    workspace = ProjectWorkspaceResolver(projects, tmp_path).resolve("project-1")

    async def flow():
        handle = await workspace.begin(
            "plan-1",
            "task-1",
            1,
            cycle_id="cycle-1",
            goal_id="goal-1",
            run_id="run-1",
        )
        await workspace.discard(handle)

    asyncio.run(flow())

    assert _git(repo, "rev-parse", "cycle/cycle-1") == _git(repo, "rev-parse", "trunk")


def test_project_resolver_rebuilds_cache_after_repo_url_changes(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    _init_repo(first, "trunk")
    _init_repo(second, "stable")
    projects = MutableProjects(
        ProjectDefinition(id="project-1", name="Project", repo_url=str(first))
    )
    resolver = ProjectWorkspaceResolver(projects, tmp_path)
    first_workspace = resolver.resolve("project-1")

    async def run_in(workspace, plan_id):
        handle = await workspace.begin(plan_id, "task-1", 1)
        await workspace.discard(handle)

    asyncio.run(run_in(first_workspace, "plan-first"))
    projects.project = projects.project.model_copy(update={"repo_url": str(second)})
    second_workspace = resolver.resolve("project-1")
    asyncio.run(run_in(second_workspace, "plan-second"))

    assert second_workspace is not first_workspace
    assert _git(first, "branch", "--list", "plan/plan-second") == ""
    assert _git(second, "branch", "--list", "plan/plan-second") == "plan/plan-second"


def test_remote_repo_url_changes_use_distinct_clone_destinations(tmp_path):
    projects = MutableProjects(
        ProjectDefinition(
            id="project-1",
            name="Project",
            repo_url="https://git.example.test/one.git",
        )
    )
    resolver = ProjectWorkspaceResolver(projects, tmp_path)
    first = resolver._repository_path(projects.project)
    projects.project = projects.project.model_copy(
        update={"repo_url": "https://git.example.test/two.git"}
    )
    second = resolver._repository_path(projects.project)

    assert first != second
    assert first.parent == second.parent
