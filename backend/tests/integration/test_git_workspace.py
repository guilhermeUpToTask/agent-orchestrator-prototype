"""GitBranchWorkspace on a real git repo: commit lands on the plan branch,
discard is a true rollback, retries begin clean (stateless task execution)."""

from __future__ import annotations

import asyncio
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.app.testing.fakes import FakeClock
from src.infra.git.workspace import GitBranchWorkspace, LocalDirWorkspace
from src.infra.runtime.verification_executor import LocalVerificationExecutor

pytestmark = pytest.mark.integration


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


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
