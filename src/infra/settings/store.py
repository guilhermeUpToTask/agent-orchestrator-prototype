"""
src/infra/settings/store.py — JSON persistence for configuration files.

Two concrete stores:
  GlobalConfigStore   — manages ~/.orchestrator/config.json  (MachineSettings)
  ProjectConfigStore  — manages <project_home>/project.json  (ProjectSettings)

Rules:
  - Secret fields are stripped before any write (defense-in-depth).
  - Extra keys written by users are preserved on round-trip loads.
  - Writes are atomic via AtomicFileWriter (fsync + rename).
  - Errors are handled consistently (corrupt/missing → return defaults).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from src.infra.fs.atomic_writer import AtomicFileWriter
from src.infra.settings.defaults import (
    GLOBAL_CONFIG_FILENAME,
    MACHINE_DEFAULTS,
    MACHINE_PERSISTABLE_KEYS,
    PROJECT_CONFIG_FILENAME,
    PROJECT_DEFAULTS,
)
from src.infra.settings.models import MachineSettings, ProjectSettings

_SECRET_KEYS: frozenset[str] = frozenset(
    {"github_token", "anthropic_api_key", "gemini_api_key", "openrouter_api_key"}
)


def _strip_secrets(data: dict[str, Any]) -> dict[str, Any]:
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

    Writes are atomic — a crash mid-write leaves the previous file intact.
    Only stores MACHINE_PERSISTABLE_KEYS (project_name, redis_url).
    """

    def __init__(self, home: Path | None = None) -> None:
        self._home = Path(home) if home is not None else _resolve_orchestrator_home()
        self._path = self._home / GLOBAL_CONFIG_FILENAME

    @property
    def config_path(self) -> Path:
        return self._path

    @property
    def orchestrator_dir(self) -> Path:
        return self._home

    def exists(self) -> bool:
        return self._path.exists()

    def load(self) -> dict[str, Any]:
        """
        Return the raw JSON dict from disk, merged with defaults.
        Returns defaults unchanged if the file is absent or corrupt.
        Secrets are stripped on read as defense-in-depth.
        """
        base = {k: v for k, v in MACHINE_DEFAULTS.items() if k in MACHINE_PERSISTABLE_KEYS}
        if not self._path.exists():
            return base
        try:
            on_disk = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return base

        base.update(_strip_secrets(on_disk))
        return base

    # Alias kept for callers that used the old name
    def load_raw(self) -> dict[str, Any]:
        return self.load()

    def save(self, data: dict[str, Any]) -> None:
        """
        Atomically persist data to config.json. Secrets are stripped.
        """
        safe = _strip_secrets(data)
        AtomicFileWriter.write_text(self._path, json.dumps(safe, indent=2, default=str))

    def update(self, **kwargs: Any) -> None:
        """Merge kwargs into the existing config, preserving unknown keys."""
        current = self.load()
        current.update({k: v for k, v in _strip_secrets(kwargs).items() if v is not None})
        self.save(current)

    def initialize(self) -> dict[str, Any]:
        """
        Write defaults and return them. Called on first run / --defaults flag.
        Replaces the old generate_defaults() name.
        """
        data = {k: v for k, v in MACHINE_DEFAULTS.items()
                if k in MACHINE_PERSISTABLE_KEYS and v is not None}
        self.save(data)
        return data

    # Backward-compat alias
    def generate_defaults(self) -> dict[str, Any]:
        return self.initialize()


# ---------------------------------------------------------------------------
# ProjectConfigStore
# ---------------------------------------------------------------------------

class ProjectConfigStore:
    """
    Read/write <project_home>/project.json.

    Writes are atomic. github_token is never persisted.
    """

    def __init__(self, project_home: Path) -> None:
        self._path = project_home / PROJECT_CONFIG_FILENAME

    @property
    def settings_path(self) -> Path:
        return self._path

    def exists(self) -> bool:
        return self._path.exists()

    def load(self) -> ProjectSettings:
        """Return ProjectSettings from disk. Falls back to defaults on error."""
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
        Atomically persist settings to project.json.
        Secrets stripped. Extra user keys preserved.
        """
        existing: dict[str, Any] = {}
        if self._path.exists():
            try:
                existing = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

        existing.update(settings.to_dict())
        safe = _strip_secrets(existing)
        AtomicFileWriter.write_text(self._path, json.dumps(safe, indent=2, default=str))
