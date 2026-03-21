"""
src/infra/config.py — Centralised orchestrator configuration.

All paths derive from two variables:
  ORCHESTRATOR_HOME  — global home dir, default ~/.orchestrator
  PROJECT_NAME       — project context, default "default"

Resulting structure:
  ~/.orchestrator/
    projects/
      <project_name>/
        tasks/
        agents/registry.json
        repo/          ← bare clone of target repo (agents push branches here)
        workspaces/    ← ephemeral per-task clones
        logs/
        events/

This mirrors how tools like .gemini or .claude work — one global home,
one subdirectory per project. The init wizard populates this structure.

Env-var reference:
  AGENT_MODE          → mode
  AGENT_ID            → agent_id
  REDIS_URL           → redis_url
  TASK_TIMEOUT_SECONDS→ task_timeout
  ORCHESTRATOR_HOME   → orchestrator_home
  PROJECT_NAME        → project_name
  REPO_URL            → repo_url            (optional — derived if absent)
  TASKS_DIR           → tasks_dir           (optional — derived if absent)
  REGISTRY_PATH       → registry_path       (optional — derived if absent)
  WORKSPACE_DIR       → workspace_dir       (optional — derived if absent)
  LOGS_DIR            → logs_dir            (optional — derived if absent)
  EVENTS_DIR          → events_dir          (optional — derived if absent)
  ANTHROPIC_API_KEY   → anthropic_api_key
  GEMINI_API_KEY      → gemini_api_key
  OPENROUTER_API_KEY  → openrouter_api_key
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional, Tuple, Type

from pydantic import AliasChoices, Field, SecretStr, model_validator
from src.infra.project_paths import ProjectPaths
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class LocalJsonConfigSource(PydanticBaseSettingsSource):
    """
    Loads project-scoped config from .orchestrator/config.json in the CWD.

    Priority is below env vars / .env but above field defaults, so the wizard
    values persist without ever touching environment variables.

    Keys understood (mirrors managed keys in OrchestratorConfigManager):
      project_name      → OrchestratorConfig.project_name
      redis_url         → OrchestratorConfig.redis_url
    """

    def _read(self) -> dict[str, Any]:
        path = Path.cwd() / ".orchestrator" / "config.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        data = self._read()
        if field_name in data and data[field_name] is not None:
            return data[field_name], field_name, False
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        return {k: v for k, v in self._read().items() if v is not None}


class OrchestratorConfig(BaseSettings):
    """
    Single source of truth for all static orchestrator configuration.

    pydantic-settings resolves values in priority order:
      1. Values passed directly to the constructor (useful in tests)
      2. Environment variables
      3. .env file (silently ignored if missing)
      4. Field defaults
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,  # constructor kwargs — highest priority
            env_settings,  # environment variables
            dotenv_settings,  # .env file
            LocalJsonConfigSource(settings_cls),  # .orchestrator/config.json
            file_secret_settings,  # /run/secrets (Docker etc.) — lowest
        )

    # ── Orchestrator ─────────────────────────────────────────────────────────

    mode: str = Field(
        "dry-run",
        validation_alias=AliasChoices("AGENT_MODE", "mode"),
        description="'dry-run' uses in-memory stubs; 'real' uses Redis + live CLIs.",
    )

    agent_id: str = Field(
        "agent-worker-001",
        validation_alias=AliasChoices("AGENT_ID", "agent_id"),
        description="Identity of this worker process (used for lease ownership).",
    )

    # ── Infrastructure ────────────────────────────────────────────────────────

    redis_url: str = Field(
        "redis://localhost:6379/0",
        validation_alias=AliasChoices("REDIS_URL", "redis_url"),
    )

    task_timeout: int = Field(
        600,
        validation_alias=AliasChoices("TASK_TIMEOUT_SECONDS", "task_timeout"),
        description="Max seconds to wait for an agent CLI to complete a task.",
    )

    # ── Home + project ────────────────────────────────────────────────────────

    orchestrator_home: Path = Field(
        default_factory=lambda: Path.home() / ".orchestrator",
        validation_alias=AliasChoices("ORCHESTRATOR_HOME", "orchestrator_home"),
        description="Global home dir — same role as ~/.gemini or ~/.claude.",
    )

    project_name: str = Field(
        "default",
        validation_alias=AliasChoices("PROJECT_NAME", "project_name"),
        description="Active project context. All paths are scoped under projects/<name>/.",
    )

    # ── Paths (all derived from orchestrator_home/projects/project_name) ─────
    # Set any of these to override the derived value.

    tasks_dir: Optional[Path] = Field(
        None,
        validation_alias=AliasChoices("TASKS_DIR", "tasks_dir"),
    )
    registry_path: Optional[Path] = Field(
        None,
        validation_alias=AliasChoices("REGISTRY_PATH", "registry_path"),
    )
    repo_url: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("REPO_URL", "repo_url"),
    )
    # Deprecated: source_repo_url has moved to ProjectSettings (project.json).
    # This field is kept ONLY to support SOURCE_REPO_URL env-var overrides.
    # The factory reads ProjectSettings first and falls back to this field.
    source_repo_url: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("SOURCE_REPO_URL", "source_repo_url"),
        description="Deprecated: use ProjectSettings (project.json). Kept for SOURCE_REPO_URL env-var compat.",
    )
    workspace_dir: Optional[Path] = Field(
        None,
        validation_alias=AliasChoices("WORKSPACE_DIR", "workspace_dir"),
    )
    logs_dir: Optional[Path] = Field(
        None,
        validation_alias=AliasChoices("LOGS_DIR", "logs_dir"),
    )
    events_dir: Optional[Path] = Field(
        None,
        validation_alias=AliasChoices("EVENTS_DIR", "events_dir"),
    )
    goals_dir: Optional[Path] = Field(
        None,
        validation_alias=AliasChoices("GOALS_DIR", "goals_dir"),
    )

    # ── API Keys ──────────────────────────────────────────────────────────────

    anthropic_api_key: SecretStr = Field(
        SecretStr(""),
        validation_alias=AliasChoices("ANTHROPIC_API_KEY", "anthropic_api_key"),
        description="Used by claude and pi (anthropic backend) runtimes.",
    )

    gemini_api_key: SecretStr = Field(
        SecretStr(""),
        validation_alias=AliasChoices("GEMINI_API_KEY", "gemini_api_key"),
        description="Used by gemini and pi (gemini backend) runtimes.",
    )

    openrouter_api_key: SecretStr = Field(
        SecretStr(""),
        validation_alias=AliasChoices("OPENROUTER_API_KEY", "openrouter_api_key"),
        description="Used by pi (openrouter backend) runtime.",
    )

    # ── Path resolution ───────────────────────────────────────────────────────

    @model_validator(mode="after")
    def _resolve_derived_paths(self) -> "OrchestratorConfig":
        """
        Derive all paths by delegating to ProjectPaths.
        Any field set explicitly (via env var or constructor) is left as-is —
        overrides still work for power users and tests.
        """
        paths = ProjectPaths.for_project(self.orchestrator_home, self.project_name)

        if self.tasks_dir is None:
            self.tasks_dir = paths.tasks_dir
        if self.registry_path is None:
            self.registry_path = paths.registry_path
        if self.repo_url is None:
            self.repo_url = paths.repo_url
        if self.workspace_dir is None:
            self.workspace_dir = paths.workspace_dir
        if self.logs_dir is None:
            self.logs_dir = paths.logs_dir
        if self.events_dir is None:
            self.events_dir = paths.events_dir
        if self.goals_dir is None:
            self.goals_dir = paths.goals_dir
        return self

    @property
    def project_home(self) -> Path:
        """Convenience accessor for the active project directory."""
        return self.orchestrator_home / "projects" / self.project_name

    @property
    def paths(self) -> ProjectPaths:
        """
        The fully-resolved ProjectPaths for the active project.

        Prefer this over accessing individual path fields directly — it makes
        clear that these paths belong to the project, not the orchestrator.
        """
        return ProjectPaths.for_project(self.orchestrator_home, self.project_name)

    @property
    def home_dir(self) -> str:
        """str alias for orchestrator_home — kept for backward compatibility."""
        return str(self.orchestrator_home)

    @classmethod
    def from_env(cls) -> "OrchestratorConfig":
        """Kept for backward compatibility."""
        return cls()


config = OrchestratorConfig()
