"""
src/infra/settings/models.py — Typed configuration models.

Three explicit concerns, kept separate:

  MachineSettings   — global orchestrator behaviour (mode, timeouts, redis_url,
                       orchestrator_home, project_name).  Persisted in config.json.
  ProjectSettings   — non-secret, per-project data (source_repo_url, github_owner,
                       github_repo, github_base_branch).  Persisted in project.json.
  SecretSettings    — credentials that must NEVER be written to any JSON file.
                       Loaded only from env / .env / Docker secrets.
  ProjectPaths      — derived filesystem layout; computed, never stored.

Rule: only MachineSettings and ProjectSettings may be persisted.
      SecretSettings is env-only.  ProjectPaths is computed on demand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# MachineSettings — orchestrator-global, per-machine, non-secret
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MachineSettings:
    """
    Global orchestrator runtime settings.

    These are shared across all projects on this machine and are safe to
    persist in ~/.orchestrator/config.json (they contain no secrets).

    Attributes
    ----------
    mode:
        "dry-run" uses in-memory stubs; "real" activates Redis and live CLIs.
    agent_id:
        Identity of this worker process (used for lease ownership).
    redis_url:
        Redis connection string; only relevant in "real" mode.
    task_timeout:
        Maximum seconds to wait for an agent CLI to complete a task.
    orchestrator_home:
        Global home directory; mirrors ~/.gemini or ~/.claude conventions.
    project_name:
        Active project context.  All project-scoped paths are derived from this.
    """

    mode: str = "dry-run"
    agent_id: str = "agent-worker-001"
    redis_url: str = "redis://localhost:6379/0"
    task_timeout: int = 600
    orchestrator_home: Path = field(
        default_factory=lambda: Path.home() / ".orchestrator"
    )
    project_name: str | None = None

    # Keys that the config store is allowed to persist (no secrets, no derived values)
    PERSISTABLE_KEYS: frozenset[str] = field(
        default=frozenset({"project_name", "redis_url"}),
        init=False,
        repr=False,
        compare=False,
    )

    def to_persistable_dict(self) -> dict:
        """Return only the keys safe to write to config.json."""
        return {
            k: v
            for k, v in {
                "project_name": self.project_name,
                "redis_url": self.redis_url,
            }.items()
            if v is not None
        }


# ---------------------------------------------------------------------------
# ProjectSettings — per-project, non-secret
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProjectSettings:
    """
    Non-secret settings specific to a single orchestrated project.

    Safe to persist in project.json.  Must NOT contain any credentials.

    Attributes
    ----------
    source_repo_url:
        Upstream git repo cloned on first init (SSH or HTTPS URL).
    github_owner:
        GitHub owner (username or org) for the target repository.
    github_repo:
        GitHub repository name, without the owner prefix.
    github_base_branch:
        Branch that goal PRs target (default: "main").
    """

    source_repo_url: str | None = None
    github_owner: str | None = None
    github_repo: str | None = None
    github_base_branch: str = "main"

    @property
    def github_configured(self) -> bool:
        """Return True if the non-secret GitHub fields are set."""
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
# SecretSettings — env-only, never persisted
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SecretSettings:
    """
    Credentials and tokens loaded exclusively from environment variables.

    This dataclass must NEVER be serialised or written to any file.

    Attributes
    ----------
    anthropic_api_key:
        Used by "claude" and "pi (anthropic)" runtimes.  Env: ANTHROPIC_API_KEY.
    gemini_api_key:
        Used by "gemini" and "pi (gemini)" runtimes.  Env: GEMINI_API_KEY.
    openrouter_api_key:
        Used by "pi (openrouter)" runtime.  Env: OPENROUTER_API_KEY.
    github_token:
        GitHub PAT for the PR-driven workflow.  Env: GITHUB_TOKEN.
        Moved here from ProjectSettings to prevent accidental persistence.
    """

    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    openrouter_api_key: str = ""
    github_token: str = ""

    def __repr__(self) -> str:
        """Never expose secret values in repr output."""
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
# SettingsContext — the single injectable unit
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SettingsContext:
    """
    Single object that bundles all configuration concerns.

    Application code should receive this via constructor injection.
    It must not import any settings module directly.

    Attributes
    ----------
    machine:
        Global, per-machine orchestrator settings.
    project:
        Per-project non-secret settings.
    secrets:
        Credentials loaded from environment only.
    """

    machine: MachineSettings = field(default_factory=MachineSettings)
    project: ProjectSettings = field(default_factory=ProjectSettings)
    secrets: SecretSettings = field(default_factory=SecretSettings)

    # ------------------------------------------------------------------
    # Convenience delegations (avoids double-indirection at call sites)
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
