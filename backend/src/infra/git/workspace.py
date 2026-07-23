"""
src/infra/git/workspace.py — Workspace adapters (the async Workspace port).

GitBranchWorkspace is THE ROLLBACK MECHANISM (roadmap 2.7): every attempt runs
in its own git worktree on its own task branch —

  begin   -> branch task/<task_id>/a<attempt> off the plan branch + worktree
  commit  -> commit the worktree, --no-ff merge the task branch into the plan
             branch, clean up
  discard -> remove the worktree + delete the task branch: the plan branch is
             untouched, as if the attempt never happened

Each plan gets its own branch (plan/<plan_id>, created off the default branch
on first use) so iterations later map onto git-flow releases. Stateless task
execution falls out: a retry begins from the plan branch, never from a dirty
tree.

All git operations are the blocking subprocess CLI (predictable behaviour, no
gitpython state), hopped off the event loop via asyncio.to_thread — a long git
operation never blocks the worker's loop.

LocalDirWorkspace is the "go straight to the repo dir" output strategy: no
isolation, no rollback (discard is a no-op) — for trivial/dry runs only.
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import structlog

from src.app.ports import TaskFailed, WorkspaceHandle
from src.domain.value_objects.lifecycle import FailureKind

log = structlog.get_logger(__name__)

_GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "orchestrator",
    "GIT_AUTHOR_EMAIL": "orchestrator@local",
    "GIT_COMMITTER_NAME": "orchestrator",
    "GIT_COMMITTER_EMAIL": "orchestrator@local",
}


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **_GIT_IDENTITY},
    )
    return result.stdout.strip()


def _git_ok(repo: Path, *args: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True).returncode == 0


@dataclass
class GitWorkspaceHandle:
    path: str  # the worktree directory the agent works in
    plan_branch: str
    task_branch: str
    base_ref: str


class GitBranchWorkspace:
    def __init__(self, repo_dir: Path, default_branch: str = "main") -> None:
        self._repo = Path(repo_dir)
        self._default_branch = default_branch

    # ---- Workspace port ----
    async def begin(
        self,
        plan_id: str,
        task_id: str,
        attempt: int,
        *,
        cycle_id: str | None = None,
        goal_id: str | None = None,
        run_id: str | None = None,
        base_ref: str | None = None,
    ) -> GitWorkspaceHandle:
        return await asyncio.to_thread(
            self._begin_sync,
            plan_id,
            task_id,
            attempt,
            cycle_id,
            goal_id,
            run_id,
            base_ref,
        )

    async def snapshot(self, handle: WorkspaceHandle) -> str:
        assert isinstance(handle, GitWorkspaceHandle)
        return await asyncio.to_thread(self._snapshot_sync, handle)

    async def main_repo_status(self) -> set[str]:
        """Return the main repository's cheap porcelain working-tree fingerprint."""
        return await asyncio.to_thread(self._main_repo_status_sync)

    async def checkpoint(self, handle: WorkspaceHandle) -> str:
        assert isinstance(handle, GitWorkspaceHandle)
        commit_sha = await self.snapshot(handle)
        await asyncio.to_thread(self._remove_task_worktree, handle)
        return commit_sha

    async def merge_goal(self, plan_id: str, cycle_id: str, goal_id: str) -> str:
        return await asyncio.to_thread(self._merge_goal_sync, cycle_id, goal_id)

    async def commit(self, handle: WorkspaceHandle) -> None:
        assert isinstance(handle, GitWorkspaceHandle)
        await asyncio.to_thread(self._commit_sync, handle)

    async def discard(self, handle: WorkspaceHandle) -> None:
        assert isinstance(handle, GitWorkspaceHandle)
        await asyncio.to_thread(self._discard_sync, handle)

    async def prune(self) -> None:
        """Remove only stale worktree metadata; never delete live branches."""
        await asyncio.to_thread(self._prune_sync)

    async def audit(self) -> dict[str, list[str]]:
        return await asyncio.to_thread(self._audit_sync)

    # ---- sync internals (worker thread) ----
    def _ensure_repo(self) -> None:
        if not (self._repo / ".git").exists():
            self._repo.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "init", str(self._repo)], check=True, capture_output=True)
            _git(self._repo, "checkout", "-B", self._default_branch)
            _git(self._repo, "commit", "--allow-empty", "-m", "chore: initial commit")
            log.info("workspace.repo_seeded", repo=str(self._repo))

    def _prune_sync(self) -> None:
        if (self._repo / ".git").exists():
            _git(self._repo, "worktree", "prune")

    def _main_repo_status_sync(self) -> set[str]:
        if not (self._repo / ".git").exists():
            return set()
        return set(_git(self._repo, "status", "--porcelain").splitlines())

    def _audit_sync(self) -> dict[str, list[str]]:
        if not (self._repo / ".git").exists():
            return {"worktrees": [], "branches": []}
        worktrees = _git(self._repo, "worktree", "list", "--porcelain").splitlines()
        branches = _git(
            self._repo,
            "for-each-ref",
            "--format=%(refname:short)",
            "refs/heads/plan",
            "refs/heads/cycle",
            "refs/heads/goal",
            "refs/heads/task",
        ).splitlines()
        return {"worktrees": worktrees, "branches": branches}

    def _begin_sync(
        self,
        plan_id: str,
        task_id: str,
        attempt: int,
        cycle_id: str | None,
        goal_id: str | None,
        run_id: str | None,
        base_ref: str | None,
    ) -> GitWorkspaceHandle:
        self._ensure_repo()
        if cycle_id is not None and goal_id is not None and run_id is not None:
            cycle_branch = f"cycle/{cycle_id}"
            plan_branch = f"goal/{goal_id}"
            task_branch = f"task/{task_id}/{run_id}"
            if not _git_ok(self._repo, "rev-parse", "--verify", cycle_branch):
                _git(self._repo, "branch", cycle_branch, self._default_branch)
            if not _git_ok(self._repo, "rev-parse", "--verify", plan_branch):
                _git(self._repo, "branch", plan_branch, cycle_branch)
        else:
            plan_branch = f"plan/{plan_id}"
            task_branch = f"task/{task_id}/a{attempt}"
            if not _git_ok(self._repo, "rev-parse", "--verify", plan_branch):
                _git(self._repo, "branch", plan_branch, self._default_branch)
                log.info("workspace.plan_branch_created", branch=plan_branch)

        # -f: a stale branch from a crashed prior run of this attempt is reset,
        # so begin is idempotent (stateless task execution).
        task_base = _git(self._repo, "rev-parse", base_ref or plan_branch)
        _git(self._repo, "branch", "-f", task_branch, task_base)
        worktree = tempfile.mkdtemp(prefix=f"task-{task_id}-a{attempt}-")
        # the empty mkdtemp dir must not exist for `worktree add`
        os.rmdir(worktree)
        try:
            _git(self._repo, "worktree", "add", worktree, task_branch)
        except subprocess.CalledProcessError as exc:
            raise TaskFailed(
                f"workspace begin failed: {exc.stderr}", FailureKind.TOOL_ERROR
            ) from exc
        log.info(
            "workspace.begun",
            plan_id=plan_id,
            task_id=task_id,
            attempt=attempt,
            worktree=worktree,
        )
        return GitWorkspaceHandle(
            path=worktree,
            plan_branch=plan_branch,
            task_branch=task_branch,
            base_ref=task_base,
        )

    def _snapshot_sync(self, handle: GitWorkspaceHandle) -> str:
        wt = Path(handle.path)
        _git(wt, "add", "-A")
        if _git(wt, "status", "--porcelain"):
            _git(wt, "commit", "-m", f"task: {handle.task_branch}")
        return _git(wt, "rev-parse", "HEAD")

    @contextmanager
    def _cycle_merge_lock(self, cycle_id: str) -> Iterator[None]:
        """Cross-process advisory lock serializing merges into ONE cycle branch.

        `git worktree add` refuses to check out a branch that is already
        checked out in another worktree. Goal-level parallelism (ADR-001) runs
        one worker PROCESS per goal, so two goals finishing around the same
        time can each call merge_goal and race to check out the SAME
        cycle/<cycle_id> branch into a throwaway merge worktree. An in-process
        asyncio.Lock would not help — these are separate OS processes, and
        this local-first architecture has no network-coordinated locking
        (no Redis). A POSIX advisory flock on a per-cycle lock file under the
        repo's own .git directory (never part of the working tree, so it
        never dirties `git status`) serializes exactly the worktree-add
        through worktree-remove window, scoped to the cycle_id so unrelated
        cycles never block each other. This runs inside asyncio.to_thread
        already, so a blocking flock here is off the event loop and safe.
        """
        lock_dir = self._repo / ".git" / "cycle-locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_dir / f"{cycle_id}.lock"), os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _merge_goal_sync(self, cycle_id: str, goal_id: str) -> str:
        cycle_branch = f"cycle/{cycle_id}"
        goal_branch = f"goal/{goal_id}"
        if not _git_ok(self._repo, "rev-parse", "--verify", goal_branch):
            raise TaskFailed(
                f"goal branch is missing: {goal_branch}",
                FailureKind.TOOL_ERROR,
            )
        with self._cycle_merge_lock(cycle_id):
            merge_wt = tempfile.mkdtemp(prefix="cycle-merge-")
            os.rmdir(merge_wt)
            # A crash between `worktree add` and `worktree remove` leaves a
            # stale registration that wedges later merges ("already checked
            # out"). Prune once and retry once inside the held flock.
            try:
                _git(self._repo, "worktree", "add", merge_wt, cycle_branch)
            except subprocess.CalledProcessError:
                self._prune_sync()
                log.info(
                    "workspace.merge_worktree_pruned",
                    cycle_id=cycle_id,
                    cycle_branch=cycle_branch,
                )
                _git(self._repo, "worktree", "add", merge_wt, cycle_branch)
            try:
                _git(
                    Path(merge_wt),
                    "merge",
                    "--no-ff",
                    goal_branch,
                    "-m",
                    f"merge: {goal_branch} into {cycle_branch}",
                )
                return _git(Path(merge_wt), "rev-parse", "HEAD")
            finally:
                _git(self._repo, "worktree", "remove", "--force", merge_wt)

    def _commit_sync(self, handle: GitWorkspaceHandle) -> None:
        self._snapshot_sync(handle)
        # merge into the plan branch via a throwaway worktree (the plan branch
        # is never checked out anywhere permanent)
        merge_wt = tempfile.mkdtemp(prefix="plan-merge-")
        os.rmdir(merge_wt)
        _git(self._repo, "worktree", "add", merge_wt, handle.plan_branch)
        try:
            _git(
                Path(merge_wt),
                "merge",
                "--no-ff",
                handle.task_branch,
                "-m",
                f"merge: {handle.task_branch} into {handle.plan_branch}",
            )
        finally:
            _git(self._repo, "worktree", "remove", "--force", merge_wt)
        self._remove_task_worktree(handle)
        # the --no-ff merge preserves the attempt in history; the branch ref
        # itself is no longer needed
        subprocess.run(
            ["git", "-C", str(self._repo), "branch", "-D", handle.task_branch],
            capture_output=True,
        )
        log.info(
            "workspace.committed",
            task_branch=handle.task_branch,
            plan_branch=handle.plan_branch,
        )

    def _discard_sync(self, handle: GitWorkspaceHandle) -> None:
        """The rollback: nothing the agent did reaches the plan branch."""
        self._remove_task_worktree(handle)
        subprocess.run(
            ["git", "-C", str(self._repo), "branch", "-D", handle.task_branch],
            capture_output=True,
        )
        log.info("workspace.discarded", task_branch=handle.task_branch)

    def _remove_task_worktree(self, handle: GitWorkspaceHandle) -> None:
        subprocess.run(
            ["git", "-C", str(self._repo), "worktree", "remove", "--force", handle.path],
            capture_output=True,
        )
        shutil.rmtree(handle.path, ignore_errors=True)


@dataclass
class LocalDirHandle:
    path: str
    base_ref: str | None = None


class LocalDirWorkspace:
    """No isolation, no rollback — the agent works directly in the directory."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    async def begin(
        self,
        plan_id: str,
        task_id: str,
        attempt: int,
        *,
        cycle_id: str | None = None,
        goal_id: str | None = None,
        run_id: str | None = None,
        base_ref: str | None = None,
    ) -> LocalDirHandle:
        return LocalDirHandle(path=str(self._root))

    async def snapshot(self, handle: WorkspaceHandle) -> str:
        return "local-directory"

    async def checkpoint(self, handle: WorkspaceHandle) -> str:
        return await self.snapshot(handle)

    async def merge_goal(self, plan_id: str, cycle_id: str, goal_id: str) -> str:
        return "local-directory"

    async def commit(self, handle: WorkspaceHandle) -> None:
        pass

    async def discard(self, handle: WorkspaceHandle) -> None:
        pass

    async def prune(self) -> None:
        pass

    async def audit(self) -> dict[str, list[str]]:
        return {"worktrees": [], "branches": []}
