"""
src/infra/config.py — Centralised orchestrator configuration.

Loaded from environment variables and/or a .env file via pydantic-settings.
All static, non-runtime configuration lives here — including API keys, paths,
and infrastructure coordinates. Dynamic values (task IDs, agent assignments,
lease tokens) live in domain models and the task repository.

Env-var mapping (field name → env var):
  mode              → AGENT_MODE
  agent_id          → AGENT_ID
  redis_url         → REDIS_URL
  task_timeout      → TASK_TIMEOUT_SECONDS
  orchestrator_home → ORCHESTRATOR_HOME
  tasks_dir         → TASKS_DIR          (optional — derived from home if absent)
  registry_path     → REGISTRY_PATH      (optional — derived from home if absent)
  repo_url          → REPO_URL           (optional — derived from home if absent)
  workspace_dir     → WORKSPACE_DIR      (optional — derived from home if absent)
  anthropic_api_key → ANTHROPIC_API_KEY
  gemini_api_key    → GEMINI_API_KEY

Usage:
  from src.infra.config import config

  config.mode                                 # "dry-run" | "real"
  config.anthropic_api_key.get_secret_value() # never logged as plaintext
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class OrchestratorConfig(BaseSettings):
    """
    Single source of truth for all static orchestrator configuration.

    pydantic-settings resolves values in priority order:
      1. Values passed directly to the constructor (useful in tests)
      2. Environment variables
      3. .env file (if present — silently ignored if missing)
      4. Field defaults
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
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

    # ── Paths ─────────────────────────────────────────────────────────────────

    orchestrator_home: Path = Field(
        default_factory=lambda: Path.home() / ".orchestrator",
        validation_alias=AliasChoices("ORCHESTRATOR_HOME", "orchestrator_home"),
        description="Root dir for all orchestrator state. Derived paths fall back to subdirs here.",
    )

    # Optional overrides — resolved from orchestrator_home in _resolve_derived_paths
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
    workspace_dir: Optional[Path] = Field(
        None,
        validation_alias=AliasChoices("WORKSPACE_DIR", "workspace_dir"),
    )

    # ── API Keys (SecretStr — never appear in logs or repr) ───────────────────

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

    # ── Derived-path resolution ───────────────────────────────────────────────

    @model_validator(mode="after")
    def _resolve_derived_paths(self) -> "OrchestratorConfig":
        """Fill in path fields not explicitly set by deriving them from orchestrator_home."""
        home = self.orchestrator_home
        if self.tasks_dir is None:
            self.tasks_dir = home / "tasks"
        if self.registry_path is None:
            self.registry_path = home / "agents" / "registry.json"
        if self.repo_url is None:
            self.repo_url = f"file://{home / 'repos' / 'my-repo'}"
        if self.workspace_dir is None:
            self.workspace_dir = home / "repos" / "workspaces"
        return self

    # ── Backward-compat ───────────────────────────────────────────────────────

    @property
    def home_dir(self) -> str:
        """str alias for orchestrator_home — kept for backward compatibility."""
        return str(self.orchestrator_home)

    @classmethod
    def from_env(cls) -> "OrchestratorConfig":
        """Construct from the environment. Kept for backward compatibility."""
        return cls()


# ---------------------------------------------------------------------------
# Global singleton — import this everywhere.
# In tests, patch individual fields via patch.object(config, "field", value).
# ---------------------------------------------------------------------------
config = OrchestratorConfig()