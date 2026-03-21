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

from src.domain import GitWorkspacePort

log = structlog.get_logger(__name__)


class GitWorkspaceAdapter(GitWorkspacePort):
    def __init__(
        self,
        workspace_base: str | Path,
        default_branch: str = "main",
        source_repo_url: str | None = None,
    ) -> None:
        self._base = Path(workspace_base)
        self._base.mkdir(parents=True, exist_ok=True)
        self._default_branch = default_branch
        self._source_repo_url = source_repo_url

    # ------------------------------------------------------------------
    # GitWorkspacePort
    # ------------------------------------------------------------------

    def create_workspace(self, repo_url: str, task_id: str) -> str:
        if repo_url.startswith("file://"):
            local_path = repo_url[len("file://") :]
            from pathlib import Path as P

            p = P(local_path)

            if not p.exists() or not (p / ".git").exists():
                p.mkdir(parents=True, exist_ok=True)
                if self._source_repo_url:
                    # Clone upstream into the local repo folder
                    log.info("git.cloning_upstream", source=self._source_repo_url, dest=local_path)
                    subprocess.run(
                        ["git", "clone", self._source_repo_url, local_path],
                        check=True,
                        capture_output=True,
                    )
                    log.info("git.upstream_cloned", path=local_path)
                else:
                    # New project — init an empty repo and seed main
                    log.info("git.repo_creating", path=local_path)
                    env = {
                        **os.environ,
                        "GIT_AUTHOR_NAME": "orchestrator",
                        "GIT_AUTHOR_EMAIL": "orchestrator@local",
                        "GIT_COMMITTER_NAME": "orchestrator",
                        "GIT_COMMITTER_EMAIL": "orchestrator@local",
                    }
                    subprocess.run(
                        ["git", "init", local_path],
                        check=True,
                        capture_output=True,
                    )
                    subprocess.run(
                        [
                            "git",
                            "-C",
                            local_path,
                            "commit",
                            "--allow-empty",
                            "-m",
                            "chore: initial commit",
                        ],
                        check=True,
                        capture_output=True,
                        env=env,
                    )
                    log.info("git.repo_created", path=local_path)

            # Allow agents to push task branches while main is checked out.
            # Safe — agents only push to task/<id>, never to main.
            subprocess.run(
                ["git", "-C", local_path, "config", "receive.denyCurrentBranch", "ignore"],
                check=True,
                capture_output=True,
            )

        ws = self._base / task_id
        if ws.exists():
            shutil.rmtree(ws)
        log.info("git.clone", repo_url=repo_url, dest=str(ws))
        self._run(["git", "clone", repo_url, str(ws)])
        return str(ws)

    def checkout_main_and_create_branch(
        self, workspace_path: str, branch_name: str, base_branch: str = "main"
    ) -> None:
        ws = workspace_path
        log.info("git.create_branch", branch=branch_name, base=base_branch, ws=ws)

        # Check if the repo has any commits at all.
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=ws,
            capture_output=True,
        )
        if result.returncode != 0:
            # Empty workspace clone — create initial commit and push base_branch to origin
            log.warning("git.empty_repo_creating_initial_commit", ws=ws)
            env = {
                **os.environ,
                "GIT_AUTHOR_NAME": "orchestrator",
                "GIT_AUTHOR_EMAIL": "orchestrator@local",
                "GIT_COMMITTER_NAME": "orchestrator",
                "GIT_COMMITTER_EMAIL": "orchestrator@local",
            }
            self._run(["git", "checkout", "-b", self._default_branch], cwd=ws)
            self._run(
                ["git", "commit", "--allow-empty", "-m", "chore: initial commit"],
                cwd=ws,
                extra_env=env,
            )
            self._run(["git", "push", "-u", "origin", self._default_branch], cwd=ws)
        else:
            # Fetch so remote tracking refs include any new branches (e.g. goal/<n>)
            self._run(["git", "fetch", "--all"], cwd=ws)
            # Check out the base branch — could be "main" or "goal/<n>"
            remote_ref = f"origin/{base_branch}"
            branch_exists = subprocess.run(
                ["git", "rev-parse", "--verify", remote_ref],
                cwd=ws, capture_output=True,
            ).returncode == 0
            if branch_exists:
                self._run(["git", "checkout", base_branch], cwd=ws)
                self._run(["git", "pull", "--ff-only"], cwd=ws)
            else:
                # base_branch doesn't exist on the remote yet — fall back to default
                log.warning(
                    "git.base_branch_not_found_falling_back",
                    base_branch=base_branch,
                    fallback=self._default_branch,
                )
                self._run(["git", "checkout", self._default_branch], cwd=ws)
                self._run(["git", "pull", "--ff-only"], cwd=ws)

        self._run(["git", "checkout", "-b", branch_name], cwd=ws)

    def create_goal_branch(self, repo_url: str, goal_branch: str) -> None:
        """
        Create the goal branch from main on the target repo.
        Uses a temporary workspace; cleans up after push.
        Idempotent: if the branch already exists remotely, returns without error.
        """
        import tempfile as _tempfile
        ws = _tempfile.mkdtemp(prefix="goal-init-")
        try:
            log.info("git.create_goal_branch", branch=goal_branch, repo=repo_url)
            self._run(["git", "clone", repo_url, ws])
            # If the branch already exists on the remote, nothing to do.
            check = subprocess.run(
                ["git", "ls-remote", "--exit-code", "--heads", "origin", goal_branch],
                cwd=ws, capture_output=True,
            )
            if check.returncode == 0:
                log.info("git.goal_branch_already_exists", branch=goal_branch)
                return
            self._run(["git", "checkout", self._default_branch], cwd=ws)
            self._run(["git", "pull", "--ff-only"], cwd=ws)
            self._run(["git", "checkout", "-b", goal_branch], cwd=ws)
            self._run(["git", "push", "-u", "origin", goal_branch], cwd=ws)
            log.info("git.goal_branch_created", branch=goal_branch)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def merge_task_into_goal(
        self,
        repo_url: str,
        task_branch: str,
        goal_branch: str,
        commit_message: str = "",
    ) -> str:
        """
        Merge task_branch into goal_branch on the target repo.
        Uses a temporary workspace, performs the merge, pushes, and cleans up.
        Returns the resulting merge commit sha.
        """
        import tempfile as _tempfile
        ws = _tempfile.mkdtemp(prefix="goal-merge-")
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME":    "orchestrator",
            "GIT_AUTHOR_EMAIL":   "orchestrator@local",
            "GIT_COMMITTER_NAME": "orchestrator",
            "GIT_COMMITTER_EMAIL":"orchestrator@local",
        }
        try:
            log.info(
                "git.merge_task_into_goal",
                task_branch=task_branch,
                goal_branch=goal_branch,
                repo=repo_url,
            )
            self._run(["git", "clone", repo_url, ws])
            self._run(["git", "fetch", "--all"], cwd=ws)
            self._run(["git", "checkout", goal_branch], cwd=ws)
            self._run(["git", "pull", "--ff-only"], cwd=ws)
            msg = commit_message or f"merge: {task_branch} into {goal_branch}"
            self._run(
                ["git", "merge", "--no-ff", f"origin/{task_branch}", "-m", msg],
                cwd=ws,
                extra_env=env,
            )
            self._run(["git", "push", "origin", goal_branch], cwd=ws)
            result = self._run(["git", "rev-parse", "HEAD"], cwd=ws, capture=True)
            sha = result.stdout.strip()
            log.info("git.merge_done", sha=sha, goal_branch=goal_branch)
            return sha
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def apply_changes_and_commit(self, workspace_path: str, commit_message: str) -> str:
        ws = workspace_path
        self._run(["git", "add", "-A"], cwd=ws)
        self._run(["git", "commit", "-m", commit_message], cwd=ws)
        result = self._run(["git", "rev-parse", "HEAD"], cwd=ws, capture=True)
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
        """
        Return all files changed in the workspace vs the base branch —
        including new untracked files, modifications, and deletions.

        Called before apply_changes_and_commit so we must look at the
        working tree, not just committed history.

        --untracked-files=all is required so git reports individual file
        paths (e.g. app/auth.py) rather than collapsing untracked
        directories to a trailing-slash summary (e.g. app/).  Without it,
        _check_allowed_files would see "app/" ≠ "app/auth.py" and produce
        a spurious forbidden-file violation for every new file in a new dir.
        """
        result = self._run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=workspace_path,
            capture=True,
        )
        return _parse_git_porcelain(result.stdout)

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
        subprocess.run(
            ["git", "-C", ws, "commit", "--allow-empty", "-m", "init"],
            check=True,
            capture_output=True,
            env={
                **os.environ,
                "GIT_AUTHOR_NAME": "test",
                "GIT_AUTHOR_EMAIL": "t@t.com",
                "GIT_COMMITTER_NAME": "test",
                "GIT_COMMITTER_EMAIL": "t@t.com",
            },
        )
        self._workspaces[task_id] = ws
        return ws

    def checkout_main_and_create_branch(
        self, workspace_path: str, branch_name: str, base_branch: str = "main"
    ) -> None:
        subprocess.run(
            ["git", "-C", workspace_path, "checkout", "-b", branch_name],
            check=True,
            capture_output=True,
        )

    def create_goal_branch(self, repo_url: str, goal_branch: str) -> None:
        # No-op in dry-run — no actual remote to push to
        pass

    def merge_task_into_goal(
        self,
        repo_url: str,
        task_branch: str,
        goal_branch: str,
        commit_message: str = "",
    ) -> str:
        # Return a fake sha in dry-run
        import hashlib, time
        fake = hashlib.sha1(f"{task_branch}{goal_branch}{time.time()}".encode()).hexdigest()
        return fake

    def apply_changes_and_commit(self, workspace_path: str, commit_message: str) -> str:
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "agent",
            "GIT_AUTHOR_EMAIL": "a@a.com",
            "GIT_COMMITTER_NAME": "agent",
            "GIT_COMMITTER_EMAIL": "a@a.com",
        }
        subprocess.run(
            ["git", "-C", workspace_path, "add", "-A"], check=True, capture_output=True, env=env
        )
        subprocess.run(
            ["git", "-C", workspace_path, "commit", "--allow-empty", "-m", commit_message],
            check=True,
            capture_output=True,
            env=env,
        )
        result = subprocess.run(
            ["git", "-C", workspace_path, "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        return result.stdout.strip()

    def push_branch(
        self, workspace_path: str, branch_name: str, remote_name: str = "origin"
    ) -> None:
        pass  # no-op in dry-run

    def cleanup_workspace(self, workspace_path: str) -> None:
        shutil.rmtree(workspace_path, ignore_errors=True)

    def get_modified_files(self, workspace_path: str) -> list[str]:
        result = subprocess.run(
            ["git", "-C", workspace_path, "status", "--porcelain", "--untracked-files=all"],
            capture_output=True,
            text=True,
        )
        return _parse_git_porcelain(result.stdout)


def _parse_git_porcelain(output: str) -> list[str]:
    """Parse output from `git status --porcelain` and return a list of modified files."""
    files = []
    for line in output.splitlines():
        if not line.strip():
            continue
        # porcelain format: "XY filename" — filename starts at col 3
        # handles renames: "R  old -> new" — take the new name after " -> "
        entry = line[3:].strip()
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        files.append(entry)
    return files
