"""
src/infra/settings/models.py — Typed configuration models.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from functools import cached_property


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ConfigurationError(RuntimeError):
    """Raised when a required configuration value is missing."""


# ---------------------------------------------------------------------------
# MachineSettings
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MachineSettings:
    """
    Global orchestrator runtime settings. Safe to persist in config.json.
    """
    mode: str = "dry-run"
    agent_id: str = "agent-worker-001"
    redis_url: str = "redis://localhost:6379/0"
    task_timeout: int = 600
    orchestrator_home: Path = field(default_factory=lambda: Path.home() / ".orchestrator")
    project_name: str | None = None

    def to_persistable_dict(self) -> dict:
        """Return only the keys safe to write to config.json."""
        return {k: v for k, v in {
            "project_name": self.project_name,
            "redis_url": self.redis_url,
        }.items() if v is not None}


# ---------------------------------------------------------------------------
# ProjectSettings
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProjectSettings:
    """
    Non-secret, per-project settings. Safe to persist in project.json.
    Must NOT contain any credentials.
    """
    source_repo_url: str | None = None
    github_owner: str | None = None
    github_repo: str | None = None
    github_base_branch: str = "main"

    @property
    def github_repo_configured(self) -> bool:
        """True if the non-secret GitHub fields (owner + repo) are set."""
        return bool(self.github_owner and self.github_repo)

    def to_dict(self) -> dict:
        return {
            "source_repo_url": self.source_repo_url,
            "github_owner": self.github_owner,
            "github_repo": self.github_repo,
            "github_base_branch": self.github_base_branch,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProjectSettings":
        return cls(
            source_repo_url=data.get("source_repo_url"),
            github_owner=data.get("github_owner"),
            github_repo=data.get("github_repo"),
            github_base_branch=data.get("github_base_branch", "main"),
        )


# ---------------------------------------------------------------------------
# SecretSettings
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SecretSettings:
    """
    Credentials loaded exclusively from environment variables.
    MUST NEVER be serialised or written to any file.
    """
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    openrouter_api_key: str = ""
    github_token: str = ""

    # ------------------------------------------------------------------
    # Explicit validation — fail fast with clear messages
    # ------------------------------------------------------------------

    def require_github_token(self) -> str:
        """Return the token or raise ConfigurationError with an actionable message."""
        if not self.github_token:
            raise ConfigurationError(
                "GITHUB_TOKEN is not set.\n"
                "Export it before running this command:\n"
                "  export GITHUB_TOKEN=ghp_..."
            )
        return self.github_token

    def require_anthropic_key(self) -> str:
        if not self.anthropic_api_key:
            raise ConfigurationError(
                "ANTHROPIC_API_KEY is not set.\n"
                "Export it before running this command:\n"
                "  export ANTHROPIC_API_KEY=sk-ant-..."
            )
        return self.anthropic_api_key

    def require_gemini_key(self) -> str:
        if not self.gemini_api_key:
            raise ConfigurationError(
                "GEMINI_API_KEY is not set.\n"
                "Export it before running this command:\n"
                "  export GEMINI_API_KEY=..."
            )
        return self.gemini_api_key

    def require_openrouter_key(self) -> str:
        if not self.openrouter_api_key:
            raise ConfigurationError(
                "OPENROUTER_API_KEY is not set.\n"
                "Export it before running this command:\n"
                "  export OPENROUTER_API_KEY=..."
            )
        return self.openrouter_api_key

    def __repr__(self) -> str:
        fields = ", ".join(
            f"{k}={'***' if v else 'unset'}"
            for k, v in {
                "anthropic_api_key": self.anthropic_api_key,
                "gemini_api_key": self.gemini_api_key,
                "openrouter_api_key": self.openrouter_api_key,
                "github_token": self.github_token,
            }.items()
        )
        return f"SecretSettings({fields})"


# ---------------------------------------------------------------------------
# SettingsContext
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SettingsContext:
    """
    Single injectable bundle of all configuration concerns.

    Application code receives this via constructor injection — never imports
    settings modules directly.
    """
    machine: MachineSettings = field(default_factory=MachineSettings)
    project: ProjectSettings = field(default_factory=ProjectSettings)
    secrets: SecretSettings = field(default_factory=SecretSettings)

    # ------------------------------------------------------------------
    # Derived paths — computed once, cached
    # ------------------------------------------------------------------

    @cached_property
    def paths(self):
        """
        Fully-resolved ProjectPaths for the active project.

        Computed once on first access and cached.  Raises ValueError if
        no project is configured.
        """
        from src.infra.project_paths import ProjectPaths
        if not self.machine.project_name:
            raise ValueError(
                "No project configured. Run `orchestrator init` first."
            )
        return ProjectPaths.for_project(
            self.machine.orchestrator_home,
            self.machine.project_name,
        )

    # ------------------------------------------------------------------
    # Convenience delegations
    # ------------------------------------------------------------------

    @property
    def mode(self) -> str:
        return self.machine.mode

    @property
    def project_name(self) -> str | None:
        return self.machine.project_name

    @property
    def orchestrator_home(self) -> Path:
        return self.machine.orchestrator_home

    @property
    def project_home(self) -> Path:
        if not self.machine.project_name:
            raise ValueError("No project configured. Run `orchestrator init` first.")
        return self.machine.orchestrator_home / "projects" / self.machine.project_name

    # ------------------------------------------------------------------
    # GitHub convenience — full check (repo config + secret)
    # ------------------------------------------------------------------

    def github_fully_configured(self) -> bool:
        """True when non-secret fields AND the token are all present."""
        return self.project.github_repo_configured and bool(self.secrets.github_token)
