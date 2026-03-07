"""
src/infra/git/workspace_adapter.py — GitWorkspacePort adapter using subprocess.

Uses the git CLI directly for predictable behaviour.
Workspaces are created in /tmp/workspaces/task-<id>/.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import structlog

from src.core.ports import GitWorkspacePort

log = structlog.get_logger(__name__)

_WORKSPACE_BASE = Path("/tmp/workspaces")


class GitWorkspaceAdapter(GitWorkspacePort):

    def __init__(
        self,
        workspace_base: str | Path = _WORKSPACE_BASE,
        default_branch: str = "main",
    ) -> None:
        self._base = Path(workspace_base)
        self._base.mkdir(parents=True, exist_ok=True)
        self._default_branch = default_branch

    # ------------------------------------------------------------------
    # GitWorkspacePort
    # ------------------------------------------------------------------

    def create_workspace(self, repo_url: str, task_id: str) -> str:
        ws = self._base / task_id
        if ws.exists():
            shutil.rmtree(ws)
        log.info("git.clone", repo_url=repo_url, dest=str(ws))
        self._run(["git", "clone", repo_url, str(ws)])
        return str(ws)

    def checkout_main_and_create_branch(
        self, workspace_path: str, branch_name: str
    ) -> None:
        ws = workspace_path
        log.info("git.create_branch", branch=branch_name, ws=ws)
        self._run(["git", "checkout", self._default_branch], cwd=ws)
        self._run(["git", "pull", "--ff-only"], cwd=ws)
        self._run(["git", "checkout", "-b", branch_name], cwd=ws)

    def apply_changes_and_commit(
        self, workspace_path: str, commit_message: str
    ) -> str:
        ws = workspace_path
        self._run(["git", "add", "-A"], cwd=ws)
        self._run(["git", "commit", "-m", commit_message], cwd=ws)
        result = self._run(
            ["git", "rev-parse", "HEAD"], cwd=ws, capture=True
        )
        commit_sha = result.stdout.strip()
        log.info("git.committed", sha=commit_sha, ws=ws)
        return commit_sha

    def push_branch(
        self,
        workspace_path: str,
        branch_name: str,
        remote_name: str = "origin",
    ) -> None:
        log.info("git.push", branch=branch_name, remote=remote_name)
        self._run(
            ["git", "push", remote_name, branch_name],
            cwd=workspace_path,
        )

    def cleanup_workspace(self, workspace_path: str) -> None:
        ws = Path(workspace_path)
        if ws.exists():
            shutil.rmtree(ws)
            log.info("git.cleanup", ws=workspace_path)

    def get_modified_files(self, workspace_path: str) -> list[str]:
        """Return files modified vs origin/main."""
        try:
            result = self._run(
                ["git", "diff", "--name-only", f"origin/{self._default_branch}...HEAD"],
                cwd=workspace_path,
                capture=True,
            )
            files = [f.strip() for f in result.stdout.splitlines() if f.strip()]
            return files
        except Exception:
            # Fallback: staged files
            result = self._run(
                ["git", "diff", "--name-only", "--cached"],
                cwd=workspace_path,
                capture=True,
            )
            return [f.strip() for f in result.stdout.splitlines() if f.strip()]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _run(
        cmd: list[str],
        cwd: str | None = None,
        capture: bool = False,
        extra_env: dict | None = None,
    ) -> subprocess.CompletedProcess:
        env = {**os.environ, **(extra_env or {})}
        result = subprocess.run(
            cmd,
            cwd=cwd,
            check=True,
            capture_output=capture,
            text=True,
            env=env,
        )
        return result


# ---------------------------------------------------------------------------
# Dry-run / stub adapter (for CI tests — no real git)
# ---------------------------------------------------------------------------

class DryRunGitWorkspaceAdapter(GitWorkspacePort):
    """
    Creates a real local git repo in a temp dir so tests can verify
    branch/commit logic without a remote.
    """

    def __init__(self) -> None:
        self._workspaces: dict[str, str] = {}

    def create_workspace(self, repo_url: str, task_id: str) -> str:
        ws = tempfile.mkdtemp(prefix=f"dryrun-task-{task_id}-")
        subprocess.run(["git", "init", ws], check=True, capture_output=True)
        subprocess.run(["git", "-C", ws, "commit", "--allow-empty", "-m", "init"],
                       check=True, capture_output=True,
                       env={**__import__("os").environ,
                            "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t.com",
                            "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t.com"})
        self._workspaces[task_id] = ws
        return ws

    def checkout_main_and_create_branch(self, workspace_path: str, branch_name: str) -> None:
        subprocess.run(["git", "-C", workspace_path, "checkout", "-b", branch_name],
                       check=True, capture_output=True)

    def apply_changes_and_commit(self, workspace_path: str, commit_message: str) -> str:
        env = {**__import__("os").environ,
               "GIT_AUTHOR_NAME": "agent", "GIT_AUTHOR_EMAIL": "a@a.com",
               "GIT_COMMITTER_NAME": "agent", "GIT_COMMITTER_EMAIL": "a@a.com"}
        subprocess.run(["git", "-C", workspace_path, "add", "-A"],
                       check=True, capture_output=True, env=env)
        subprocess.run(["git", "-C", workspace_path, "commit", "--allow-empty",
                        "-m", commit_message],
                       check=True, capture_output=True, env=env)
        result = subprocess.run(["git", "-C", workspace_path, "rev-parse", "HEAD"],
                                check=True, capture_output=True, text=True, env=env)
        return result.stdout.strip()

    def push_branch(self, workspace_path: str, branch_name: str, remote_name: str = "origin") -> None:
        pass  # no-op in dry-run

    def cleanup_workspace(self, workspace_path: str) -> None:
        shutil.rmtree(workspace_path, ignore_errors=True)

    def get_modified_files(self, workspace_path: str) -> list[str]:
        result = subprocess.run(
            ["git", "-C", workspace_path, "diff", "--name-only", "HEAD~1..HEAD"],
            capture_output=True, text=True,
        )
        return [f.strip() for f in result.stdout.splitlines() if f.strip()]
