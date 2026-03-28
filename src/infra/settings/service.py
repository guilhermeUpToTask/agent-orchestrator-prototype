"""
src/infra/settings/service.py — Settings service: the single canonical entrypoint.

Responsibilities:
  1. Load MachineSettings from env vars + .env + config.json + defaults.
  2. Load ProjectSettings from project.json + defaults.
  3. Load SecretSettings exclusively from environment variables.
  4. Merge into a SettingsContext and expose it for injection.
  5. Expose write methods only for non-secret, persistable data.

Application and domain code must NOT import from config.py, config_manager.py,
or project_settings.py directly.  They receive a SettingsContext via constructor
injection, built here or in tests.

Usage
-----
    # At the CLI/factory entry point:
    from src.infra.settings.service import SettingsService
    service = SettingsService()
    ctx = service.load()          # SettingsContext

    # Persist machine-level setting (e.g., after wizard):
    service.save_machine(project_name="my-project")

    # Persist project-level setting (e.g., after github setup):
    service.save_project(ctx.machine, project_settings)

    # Tests:
    ctx = SettingsService.for_testing(
        project_name="test",
        orchestrator_home=tmp_path,
        github_token="tok",
    )
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from src.infra.settings.defaults import MACHINE_DEFAULTS, PROJECT_DEFAULTS
from src.infra.settings.models import (
    MachineSettings,
    ProjectSettings,
    SecretSettings,
    SettingsContext,
)
from src.infra.settings.store import GlobalConfigStore, ProjectConfigStore


class SettingsService:
    """
    Assembles a SettingsContext from all configuration sources.

    Priority order (highest → lowest):
      1. Constructor / test overrides
      2. Environment variables
      3. .env file (loaded once on import by pydantic-settings, if present)
      4. config.json / project.json
      5. Field defaults

    Only secrets (GITHUB_TOKEN, *_API_KEY) are read exclusively from env.
    """

    def __init__(self, home: Path | None = None) -> None:
        self._global_store = GlobalConfigStore(home=home)

    # ------------------------------------------------------------------
    # Public: load
    # ------------------------------------------------------------------

    def load(self, project_name: str | None = None) -> SettingsContext:
        """
        Build and return the full SettingsContext.

        Parameters
        ----------
        project_name:
            Override the project name from the environment/config.json.
            Useful for CLI commands that accept --project.
        """
        machine = self._load_machine(project_name_override=project_name)
        project = self._load_project(machine)
        secrets = self._load_secrets()
        return SettingsContext(machine=machine, project=project, secrets=secrets)

    # ------------------------------------------------------------------
    # Public: persist
    # ------------------------------------------------------------------

    def save_machine(self, **kwargs: Any) -> None:
        """
        Persist allowed machine-level keys to config.json.

        Only persists keys listed in MACHINE_MANAGED_KEYS (project_name,
        redis_url).  All other keys and any secrets are silently ignored.
        """
        self._global_store.update(**kwargs)

    def save_project(
        self,
        machine: MachineSettings,
        settings: ProjectSettings,
    ) -> None:
        """
        Persist project settings to project.json.

        Secrets (github_token) are never written; the store enforces this.
        """
        if not machine.project_name:
            raise ValueError("Cannot save project settings: no project_name configured.")
        project_home = machine.orchestrator_home / "projects" / machine.project_name
        ProjectConfigStore(project_home).save(settings)

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def for_testing(
        cls,
        *,
        orchestrator_home: Path,
        project_name: str | None = None,
        mode: str = "dry-run",
        redis_url: str = "redis://localhost:6379/0",
        task_timeout: int = 600,
        github_token: str = "",
        anthropic_api_key: str = "",
        gemini_api_key: str = "",
        openrouter_api_key: str = "",
        source_repo_url: str | None = None,
        github_owner: str | None = None,
        github_repo: str | None = None,
        github_base_branch: str = "main",
    ) -> "SettingsContext":
        """
        Build a SettingsContext suitable for unit tests.

        All values are supplied explicitly — no environment reads, no disk I/O.
        """
        machine = MachineSettings(
            mode=mode,
            redis_url=redis_url,
            task_timeout=task_timeout,
            orchestrator_home=orchestrator_home,
            project_name=project_name,
        )
        project = ProjectSettings(
            source_repo_url=source_repo_url,
            github_owner=github_owner,
            github_repo=github_repo,
            github_base_branch=github_base_branch,
        )
        secrets = SecretSettings(
            github_token=github_token,
            anthropic_api_key=anthropic_api_key,
            gemini_api_key=gemini_api_key,
            openrouter_api_key=openrouter_api_key,
        )
        return SettingsContext(machine=machine, project=project, secrets=secrets)

    # ------------------------------------------------------------------
    # Internal loaders
    # ------------------------------------------------------------------

    def _load_machine(self, project_name_override: str | None = None) -> MachineSettings:
        """Build MachineSettings from env > config.json > defaults."""
        stored = self._global_store.load_raw()

        # Env vars override config.json values
        mode = os.environ.get("AGENT_MODE") or stored.get("mode") or MACHINE_DEFAULTS["mode"]
        agent_id = os.environ.get("AGENT_ID") or MACHINE_DEFAULTS["agent_id"]
        redis_url = (
            os.environ.get("REDIS_URL")
            or stored.get("redis_url")
            or MACHINE_DEFAULTS["redis_url"]
        )
        task_timeout_raw = os.environ.get("TASK_TIMEOUT_SECONDS")
        task_timeout = (
            int(task_timeout_raw) if task_timeout_raw else MACHINE_DEFAULTS["task_timeout"]
        )

        home_raw = os.environ.get("ORCHESTRATOR_HOME")
        orchestrator_home = (
            Path(home_raw).expanduser()
            if home_raw
            else MACHINE_DEFAULTS["orchestrator_home"]
        )

        project_name = (
            project_name_override
            or os.environ.get("PROJECT_NAME")
            or stored.get("project_name")
        )

        return MachineSettings(
            mode=mode,
            agent_id=agent_id,
            redis_url=redis_url,
            task_timeout=task_timeout,
            orchestrator_home=orchestrator_home,
            project_name=project_name,
        )

    def _load_project(self, machine: MachineSettings) -> ProjectSettings:
        """Build ProjectSettings from project.json > defaults."""
        if not machine.project_name:
            return ProjectSettings(**PROJECT_DEFAULTS)
        project_home = (
            machine.orchestrator_home / "projects" / machine.project_name
        )
        return ProjectConfigStore(project_home).load()

    @staticmethod
    def _load_secrets() -> SecretSettings:
        """Load secrets from env only — never from disk."""
        return SecretSettings(
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
            openrouter_api_key=os.environ.get("OPENROUTER_API_KEY", ""),
            github_token=os.environ.get("GITHUB_TOKEN", ""),
        )
