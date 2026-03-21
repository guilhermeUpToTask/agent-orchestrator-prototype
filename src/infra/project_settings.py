"""
src/infra/project_settings.py — Per-project operational settings.

Separates project-scoped configuration from orchestrator-global configuration.

Distinction:
  OrchestratorConfig  — how THIS MACHINE runs the orchestrator (redis_url,
                        api_keys, mode, agent_id, task_timeout)
  ProjectSettings     — what THIS PROJECT is (source_repo_url, etc.)
  ProjectSpec         — the domain/architecture constraints of this project
                        (tech_stack, forbidden patterns, structure)

Persisted at:
  ~/.orchestrator/projects/<project_name>/project.json

This file CAN go into version control (it contains no secrets).
OrchestratorConfig (.env / env vars) must NEVER go into version control.

Managed by ProjectSettingsManager, analogous to OrchestratorConfigManager.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


PROJECT_SETTINGS_FILENAME = "project.json"

# All keys managed here. Unknown keys written by users are preserved on load.
_MANAGED_KEYS = {"source_repo_url", "github_token", "github_owner", "github_repo", "github_base_branch"}

_DEFAULTS: dict[str, Any] = {
    "source_repo_url":    None,
    "github_token":       None,
    "github_owner":       None,
    "github_repo":        None,
    "github_base_branch": "main",
}


@dataclass
class ProjectSettings:
    """
    Operational settings for a single orchestrated project.

    Fields:
      source_repo_url   : Upstream git repo to clone on first init.
      github_token      : GitHub PAT for the PR-driven workflow.
                          Treat as a secret — do NOT commit project.json
                          if this field is set.
      github_owner      : GitHub owner (username or org) for the target repo.
      github_repo       : GitHub repository name (without owner prefix).
      github_base_branch: Branch that goal PRs target (default: "main").
    """

    source_repo_url:    str | None = None
    github_token:       str | None = None
    github_owner:       str | None = None
    github_repo:        str | None = None
    github_base_branch: str        = "main"

    @property
    def github_configured(self) -> bool:
        """Return True if all required GitHub fields are set."""
        return bool(self.github_token and self.github_owner and self.github_repo)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectSettings":
        return cls(
            source_repo_url=data.get("source_repo_url"),
            github_token=data.get("github_token"),
            github_owner=data.get("github_owner"),
            github_repo=data.get("github_repo"),
            github_base_branch=data.get("github_base_branch", "main"),
        )


class ProjectSettingsManager:
    """
    Read/write project.json for a specific project directory.

    Usage:
        manager = ProjectSettingsManager(project_home)
        settings = manager.load()
        manager.save(settings)
    """

    def __init__(self, project_home: Path) -> None:
        self._path = project_home / PROJECT_SETTINGS_FILENAME

    @property
    def settings_path(self) -> Path:
        return self._path

    def exists(self) -> bool:
        return self._path.exists()

    def load(self) -> ProjectSettings:
        """
        Return ProjectSettings from disk, falling back to defaults if the
        file is missing or unreadable.
        """
        if not self._path.exists():
            return ProjectSettings(**_DEFAULTS)
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return ProjectSettings(**_DEFAULTS)

        merged = dict(_DEFAULTS)
        merged.update(data)
        return ProjectSettings.from_dict(merged)

    def save(self, settings: ProjectSettings) -> None:
        """
        Persist settings to project.json, creating the directory if needed.
        Preserves any extra keys already in the file.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)

        # Preserve unknown keys written by users
        existing: dict[str, Any] = {}
        if self._path.exists():
            try:
                existing = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

        existing.update(settings.to_dict())
        self._path.write_text(
            json.dumps(existing, indent=2, default=str),
            encoding="utf-8",
        )
