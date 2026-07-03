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
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

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
    return (
        subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True
        ).returncode
        == 0
    )


@dataclass
class GitWorkspaceHandle:
    path: str  # the worktree directory the agent works in
    plan_branch: str
    task_branch: str


class GitBranchWorkspace:
    def __init__(self, repo_dir: Path, default_branch: str = "main") -> None:
        self._repo = Path(repo_dir)
        self._default_branch = default_branch

    # ---- Workspace port ----
    async def begin(
        self, plan_id: str, task_id: str, attempt: int
    ) -> GitWorkspaceHandle:
        return await asyncio.to_thread(self._begin_sync, plan_id, task_id, attempt)

    async def commit(self, handle: WorkspaceHandle) -> None:
        assert isinstance(handle, GitWorkspaceHandle)
        await asyncio.to_thread(self._commit_sync, handle)

    async def discard(self, handle: WorkspaceHandle) -> None:
        assert isinstance(handle, GitWorkspaceHandle)
        await asyncio.to_thread(self._discard_sync, handle)

    # ---- sync internals (worker thread) ----
    def _ensure_repo(self) -> None:
        if not (self._repo / ".git").exists():
            self._repo.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "init", str(self._repo)], check=True, capture_output=True
            )
            _git(self._repo, "checkout", "-B", self._default_branch)
            _git(self._repo, "commit", "--allow-empty", "-m", "chore: initial commit")
            log.info("workspace.repo_seeded", repo=str(self._repo))

    def _begin_sync(self, plan_id: str, task_id: str, attempt: int) -> GitWorkspaceHandle:
        self._ensure_repo()
        plan_branch = f"plan/{plan_id}"
        task_branch = f"task/{task_id}/a{attempt}"

        if not _git_ok(self._repo, "rev-parse", "--verify", plan_branch):
            _git(self._repo, "branch", plan_branch, self._default_branch)
            log.info("workspace.plan_branch_created", branch=plan_branch)

        # -f: a stale branch from a crashed prior run of this attempt is reset,
        # so begin is idempotent (stateless task execution).
        _git(self._repo, "branch", "-f", task_branch, plan_branch)
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
            path=worktree, plan_branch=plan_branch, task_branch=task_branch
        )

    def _commit_sync(self, handle: GitWorkspaceHandle) -> None:
        wt = Path(handle.path)
        _git(wt, "add", "-A")
        if _git(wt, "status", "--porcelain"):
            _git(wt, "commit", "-m", f"task: {handle.task_branch}")
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


class LocalDirWorkspace:
    """No isolation, no rollback — the agent works directly in the directory."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    async def begin(self, plan_id: str, task_id: str, attempt: int) -> LocalDirHandle:
        return LocalDirHandle(path=str(self._root))

    async def commit(self, handle: WorkspaceHandle) -> None:
        pass

    async def discard(self, handle: WorkspaceHandle) -> None:
        pass
