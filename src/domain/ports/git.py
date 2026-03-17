"""
src/domain/ports/git.py — Git workspace port.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class GitWorkspacePort(ABC):
    """
    Contract for creating and managing ephemeral git workspaces.
    Each task execution gets its own isolated clone with a dedicated branch.
    Infrastructure provides adapters (subprocess git, dry-run stub, etc.).
    """

    @abstractmethod
    def create_workspace(self, repo_url: str, task_id: str) -> str:
        """Clone the repo into an ephemeral directory. Returns workspace_path."""
        ...

    @abstractmethod
    def checkout_main_and_create_branch(
        self, workspace_path: str, branch_name: str
    ) -> None:
        """Ensure main is up to date and create a fresh task branch from it."""
        ...

    @abstractmethod
    def apply_changes_and_commit(
        self, workspace_path: str, commit_message: str
    ) -> str:
        """Stage all changes, commit, and return the resulting commit_sha."""
        ...

    @abstractmethod
    def push_branch(
        self,
        workspace_path: str,
        branch_name: str,
        remote_name: str = "origin",
    ) -> None:
        """Push the branch to the remote."""
        ...

    @abstractmethod
    def cleanup_workspace(self, workspace_path: str) -> None:
        """Delete the ephemeral workspace directory."""
        ...

    @abstractmethod
    def get_modified_files(self, workspace_path: str) -> list[str]:
        """Return relative paths of all files modified since branch creation."""
        ...
