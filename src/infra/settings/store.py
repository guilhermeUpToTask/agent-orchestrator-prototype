"""
src/infra/settings/store.py — JSON persistence for configuration files.

A single reusable abstraction for reading and writing JSON config files.
All file-system details stay inside this module.

Two concrete stores:
  GlobalConfigStore   — manages ~/.orchestrator/config.json  (MachineSettings)
  ProjectConfigStore  — manages <project_home>/project.json  (ProjectSettings)

Rules enforced here:
  - Secret fields are stripped before any write operation.
  - Extra keys written by users are preserved on round-trip loads.
  - Errors are handled consistently (corrupt/missing → return defaults).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from src.infra.settings.defaults import (
    GLOBAL_CONFIG_FILENAME,
    MACHINE_DEFAULTS,
    MACHINE_MANAGED_KEYS,
    PROJECT_CONFIG_FILENAME,
    PROJECT_DEFAULTS,
    PROJECT_MANAGED_KEYS,
)
from src.infra.settings.models import MachineSettings, ProjectSettings

# Keys that must never appear in any persisted file
_SECRET_KEYS: frozenset[str] = frozenset(
    {"github_token", "anthropic_api_key", "gemini_api_key", "openrouter_api_key"}
)


def _strip_secrets(data: dict[str, Any]) -> dict[str, Any]:
    """Remove any secret key from *data* before writing to disk."""
    return {k: v for k, v in data.items() if k not in _SECRET_KEYS}


def _resolve_orchestrator_home() -> Path:
    raw = os.environ.get("ORCHESTRATOR_HOME")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".orchestrator"


# ---------------------------------------------------------------------------
# GlobalConfigStore
# ---------------------------------------------------------------------------

class GlobalConfigStore:
    """
    Read/write ~/.orchestrator/config.json.

    Only stores the ``MACHINE_MANAGED_KEYS`` subset of MachineSettings.
    Preserves extra keys added manually by users.
    """

    def __init__(self, home: Path | None = None) -> None:
        self._home = Path(home) if home is not None else _resolve_orchestrator_home()
        self._path = self._home / GLOBAL_CONFIG_FILENAME

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def config_path(self) -> Path:
        return self._path

    @property
    def orchestrator_dir(self) -> Path:
        return self._home

    def exists(self) -> bool:
        return self._path.exists()

    def load_raw(self) -> dict[str, Any]:
        """
        Return the raw JSON dict from disk.  Missing keys are filled from
        MACHINE_DEFAULTS.  Returns defaults unchanged if the file is absent.
        """
        if not self._path.exists():
            return {k: v for k, v in MACHINE_DEFAULTS.items()
                    if k in MACHINE_MANAGED_KEYS}
        try:
            on_disk = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {k: v for k, v in MACHINE_DEFAULTS.items()
                    if k in MACHINE_MANAGED_KEYS}

        result = {k: v for k, v in MACHINE_DEFAULTS.items()
                  if k in MACHINE_MANAGED_KEYS}
        result.update(_strip_secrets(on_disk))
        return result

    def save(self, data: dict[str, Any]) -> None:
        """
        Persist *data* to config.json.  Secrets are stripped before writing.
        Creates the .orchestrator directory if it does not exist.
        """
        safe = _strip_secrets(data)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(safe, indent=2, default=str),
            encoding="utf-8",
        )

    def update(self, **kwargs: Any) -> None:
        """Merge *kwargs* into the existing config, preserving unknown keys."""
        current = self.load_raw()
        current.update({k: v for k, v in _strip_secrets(kwargs).items()
                        if v is not None})
        self.save(current)

    def generate_defaults(self) -> dict[str, Any]:
        """Write defaults and return the result.  Called on first run."""
        data = {k: v for k, v in MACHINE_DEFAULTS.items()
                if k in MACHINE_MANAGED_KEYS and v is not None}
        self.save(data)
        return data

    # Backward-compat alias used by legacy OrchestratorConfigManager callers
    def load(self) -> dict[str, Any]:
        return self.load_raw()


# ---------------------------------------------------------------------------
# ProjectConfigStore
# ---------------------------------------------------------------------------

class ProjectConfigStore:
    """
    Read/write <project_home>/project.json.

    Only stores non-secret ProjectSettings fields.
    github_token is explicitly excluded from persistence.
    """

    def __init__(self, project_home: Path) -> None:
        self._path = project_home / PROJECT_CONFIG_FILENAME

    @property
    def settings_path(self) -> Path:
        return self._path

    def exists(self) -> bool:
        return self._path.exists()

    def load(self) -> ProjectSettings:
        """
        Return ProjectSettings from disk.  Falls back to defaults on error.
        """
        if not self._path.exists():
            return ProjectSettings(**PROJECT_DEFAULTS)
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return ProjectSettings(**PROJECT_DEFAULTS)

        merged = dict(PROJECT_DEFAULTS)
        merged.update(_strip_secrets(data))
        return ProjectSettings.from_dict(merged)

    def save(self, settings: ProjectSettings) -> None:
        """
        Persist settings to project.json.  Secrets are stripped.
        Preserves extra keys already in the file.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)

        existing: dict[str, Any] = {}
        if self._path.exists():
            try:
                existing = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

        # Merge, then strip any secrets that might have crept in
        existing.update(settings.to_dict())
        safe = _strip_secrets(existing)

        self._path.write_text(
            json.dumps(safe, indent=2, default=str),
            encoding="utf-8",
        )
