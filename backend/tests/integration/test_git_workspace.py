"""GitBranchWorkspace on a real git repo: commit lands on the plan branch,
discard is a true rollback, retries begin clean (stateless task execution)."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
import threading
import time
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


def test_changed_paths_exclude_interpreter_and_test_cache_byproducts(repo):
    ws = GitBranchWorkspace(repo)
    verifier = LocalVerificationExecutor(FakeClock(datetime(2026, 7, 14, tzinfo=timezone.utc)))

    async def flow():
        handle = await ws.begin("p1", "t1", 1)
        root = Path(handle.path)
        tests = root / "tests"
        tests.mkdir()
        (tests / "test_feature.py").write_text("def test_ok():\n    assert True\n")
        pycache = tests / "__pycache__"
        pycache.mkdir()
        (pycache / "test_feature.cpython-312.pyc").write_bytes(b"\x00bytecode")
        cache = root / ".pytest_cache" / "v" / "cache"
        cache.mkdir(parents=True)
        (cache / "lastfailed").write_text("{}")
        paths = await verifier.changed_paths(handle.path, handle.base_ref)
        await ws.discard(handle)
        return paths

    assert asyncio.run(flow()) == ["tests/test_feature.py"]


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


def test_merge_goal_self_heals_stale_cycle_merge_worktree(repo):
    """Crash between worktree add and remove leaves a stale registration that
    would wedge every later merge_goal into the same cycle branch. The
    prune-then-retry path in _merge_goal_sync must clear it and succeed."""
    ws = GitBranchWorkspace(repo)

    async def setup():
        h1 = await ws.begin("p1", "t1", 1, cycle_id="c1", goal_id="g1", run_id="run-1")
        (Path(h1.path) / "feature_a.py").write_text("a\n")
        await ws.commit(h1)

        h2 = await ws.begin("p1", "t2", 1, cycle_id="c1", goal_id="g2", run_id="run-2")
        (Path(h2.path) / "feature_b.py").write_text("b\n")
        await ws.commit(h2)

    asyncio.run(setup())

    # Simulate crash: register a merge worktree for the cycle branch, then
    # delete its directory WITHOUT `git worktree remove`.
    stale_wt = tempfile.mkdtemp(prefix="cycle-merge-stale-")
    Path(stale_wt).rmdir()
    _git(repo, "worktree", "add", stale_wt, "cycle/c1")
    shutil.rmtree(stale_wt)

    sha = asyncio.run(ws.merge_goal("p1", "c1", "g1"))
    assert sha
    files = _git(repo, "ls-tree", "-r", "--name-only", "cycle/c1").splitlines()
    assert "feature_a.py" in files


def test_concurrent_merge_goal_for_same_cycle_succeeds_for_both_goals(repo, monkeypatch):
    """Two DIFFERENT worker processes each finish a different goal of the SAME
    cycle at nearly the same moment and both call merge_goal concurrently.
    Without the per-cycle flock, both race to `git worktree add` the same
    cycle branch and one hits 'fatal: already checked out'."""
    import src.infra.git.workspace as workspace_mod

    ws = GitBranchWorkspace(repo)
    real_git = workspace_mod._git

    def slow_git(repo_path, *args):
        # widen the race window right where both calls would otherwise
        # collide: checking out the shared cycle branch.
        if len(args) >= 2 and args[0] == "worktree" and args[1] == "add":
            time.sleep(0.2)
        return real_git(repo_path, *args)

    async def flow():
        h1 = await ws.begin("p1", "t1", 1, cycle_id="c1", goal_id="g1", run_id="run-1")
        (Path(h1.path) / "feature_a.py").write_text("a\n")
        await ws.commit(h1)

        h2 = await ws.begin("p1", "t2", 1, cycle_id="c1", goal_id="g2", run_id="run-2")
        (Path(h2.path) / "feature_b.py").write_text("b\n")
        await ws.commit(h2)

        monkeypatch.setattr(workspace_mod, "_git", slow_git)
        return await asyncio.gather(
            ws.merge_goal("p1", "c1", "g1"),
            ws.merge_goal("p1", "c1", "g2"),
        )

    results = asyncio.run(flow())
    assert len(results) == 2
    files = _git(repo, "ls-tree", "-r", "--name-only", "cycle/c1").splitlines()
    assert "feature_a.py" in files
    assert "feature_b.py" in files


def test_merge_goal_serializes_for_the_same_cycle_id(repo, monkeypatch):
    """Unit-level proof the flock actually blocks: two direct calls to
    _merge_goal_sync for the SAME cycle_id run one-after-another (total time
    >= two sequential holds of the artificially slowed critical section)."""
    import src.infra.git.workspace as workspace_mod

    ws = GitBranchWorkspace(repo)
    real_git = workspace_mod._git
    delay = 0.3

    def slow_git(repo_path, *args):
        if args and args[0] == "merge":
            time.sleep(delay)
        return real_git(repo_path, *args)

    async def setup():
        h1 = await ws.begin("p1", "t1", 1, cycle_id="same-cycle", goal_id="ga", run_id="run-a")
        (Path(h1.path) / "a.py").write_text("a\n")
        await ws.commit(h1)
        h2 = await ws.begin("p1", "t2", 1, cycle_id="same-cycle", goal_id="gb", run_id="run-b")
        (Path(h2.path) / "b.py").write_text("b\n")
        await ws.commit(h2)

    asyncio.run(setup())
    monkeypatch.setattr(workspace_mod, "_git", slow_git)

    results: dict[str, str] = {}

    def run_merge(goal_id):
        results[goal_id] = ws._merge_goal_sync("same-cycle", goal_id)

    start = time.monotonic()
    t1 = threading.Thread(target=run_merge, args=("ga",))
    t2 = threading.Thread(target=run_merge, args=("gb",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    elapsed = time.monotonic() - start

    assert len(results) == 2
    # serialized: the second call must wait out the first's whole locked
    # section, so total wall time is at least two sequential holds.
    assert elapsed >= delay * 2 * 0.9


def test_merge_goal_does_not_serialize_across_different_cycle_ids(repo, monkeypatch):
    """The lock is per-cycle-id: two DIFFERENT cycles must not block each
    other even though they share the same GitBranchWorkspace instance."""
    import src.infra.git.workspace as workspace_mod

    ws = GitBranchWorkspace(repo)
    real_git = workspace_mod._git
    delay = 0.3

    def slow_git(repo_path, *args):
        if args and args[0] == "merge":
            time.sleep(delay)
        return real_git(repo_path, *args)

    async def setup():
        h1 = await ws.begin("p1", "t1", 1, cycle_id="cycle-x", goal_id="gx", run_id="run-x")
        (Path(h1.path) / "x.py").write_text("x\n")
        await ws.commit(h1)
        h2 = await ws.begin("p1", "t2", 1, cycle_id="cycle-y", goal_id="gy", run_id="run-y")
        (Path(h2.path) / "y.py").write_text("y\n")
        await ws.commit(h2)

    asyncio.run(setup())
    monkeypatch.setattr(workspace_mod, "_git", slow_git)

    def run_merge(cycle_id, goal_id):
        ws._merge_goal_sync(cycle_id, goal_id)

    start = time.monotonic()
    t1 = threading.Thread(target=run_merge, args=("cycle-x", "gx"))
    t2 = threading.Thread(target=run_merge, args=("cycle-y", "gy"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    elapsed = time.monotonic() - start

    # not serialized: both critical sections overlap, so total time stays
    # well under two sequential holds.
    assert elapsed < delay * 2 * 0.9


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
