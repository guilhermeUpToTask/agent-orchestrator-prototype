"""
src/infra/config_manager.py — Manages ~/.orchestrator/config.json.

This is the global orchestrator config file that persists the values a user
sets during `orchestrator init` so they never have to touch .env or environment
variables for basic setup.

Location: ~/.orchestrator/config.json  (same root as OrchestratorConfig.orchestrator_home)

Priority order for OrchestratorConfig resolution:
  1. Constructor arguments / unit-test overrides  (highest)
  2. Environment variables
  3. .env file
  4. ~/.orchestrator/config.json            ← this module owns this layer
  5. Field defaults                          (lowest)

Keys stored in config.json (orchestrator-global, per-machine):
  project_name      — active project context
  redis_url         — Redis connection string

Note: source_repo_url has moved to project.json (ProjectSettings).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ORCHESTRATOR_DIR = ".orchestrator"
CONFIG_FILENAME = "config.json"


def _resolve_orchestrator_home() -> Path:
    """
    Resolve the orchestrator home from ORCHESTRATOR_HOME env var,
    falling back to ~/.orchestrator.
    """
    import os
    raw = os.environ.get("ORCHESTRATOR_HOME")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".orchestrator"

# Keys managed by the wizard — any extras written by users are preserved.
MANAGED_KEYS = {"project_name", "redis_url"}

DEFAULTS: dict[str, Any] = {
    "project_name": None,  # Must be set explicitly via `orchestrator init`
    "redis_url": "redis://localhost:6379/0",
}


class OrchestratorConfigManager:
    """
    Read/write ~/.orchestrator/config.json (the global orchestrator config).

    The config file always lives at:
        <orchestrator_home>/config.json
    where orchestrator_home defaults to ~/.orchestrator, matching
    OrchestratorConfig.orchestrator_home — so both always point at the
    same file.

    Typical usage:
        manager = OrchestratorConfigManager()                    # ~/.orchestrator
        manager = OrchestratorConfigManager(home=some_path)     # tests / custom home

        data = manager.load()                          # returns dict (or defaults)
        manager.save({"project_name": "my-project"})  # creates dir if needed
        manager.generate_defaults()                    # write defaults, no wizard
    """

    def __init__(self, home: Path | None = None) -> None:
        # Default to the same location OrchestratorConfig uses.
        # The `home` parameter exists only for testing and custom ORCHESTRATOR_HOME overrides.
        self._home = Path(home) if home is not None else _resolve_orchestrator_home()
        self._config_path = self._home / CONFIG_FILENAME

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def config_path(self) -> Path:
        return self._config_path

    @property
    def orchestrator_dir(self) -> Path:
        return self._home

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def exists(self) -> bool:
        """Return True if the config file is present on disk."""
        return self._config_path.exists()

    def load(self) -> dict[str, Any]:
        """
        Return the config as a dict.
        Missing keys are filled from DEFAULTS so callers always get a complete dict.
        Returns DEFAULTS unchanged if the file doesn't exist.
        """
        if not self.exists():
            return dict(DEFAULTS)
        try:
            on_disk = json.loads(self._config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return dict(DEFAULTS)

        result = dict(DEFAULTS)
        result.update(on_disk)
        return result

    def save(self, data: dict[str, Any]) -> None:
        """
        Persist *data* to config.json, creating the .orchestrator dir as needed.
        Raises OSError on write failure.
        """
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text(
            json.dumps(data, indent=2, default=str),
            encoding="utf-8",
        )

    def generate_defaults(self) -> dict[str, Any]:
        """
        Write a config.json populated with DEFAULTS and return it.
        Called when a user runs a CLI command without having run `init` first.

        Note: project_name is intentionally left as None in defaults.
        Users must run `orchestrator init` to configure a project.
        """
        data = {k: v for k, v in DEFAULTS.items() if v is not None}
        self.save(data)
        return data

    def update(self, **kwargs: Any) -> None:
        """Merge *kwargs* into the existing config, preserving unknown keys."""
        data = self.load()
        data.update({k: v for k, v in kwargs.items() if v is not None})
        self.save(data)
