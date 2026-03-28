"""
src/infra/settings — Canonical settings package.

Public surface:
  SettingsContext   — single injectable bundle of all config concerns
  MachineSettings   — global orchestrator settings (non-secret)
  ProjectSettings   — per-project settings (non-secret)
  SecretSettings    — env-only credentials (never persisted)
  SettingsService   — load/save entry point
  GlobalConfigStore — low-level JSON store for config.json
  ProjectConfigStore— low-level JSON store for project.json
"""

from src.infra.settings.models import (
    MachineSettings,
    ProjectSettings,
    SecretSettings,
    SettingsContext,
)
from src.infra.settings.service import SettingsService
from src.infra.settings.store import GlobalConfigStore, ProjectConfigStore

__all__ = [
    "MachineSettings",
    "ProjectSettings",
    "SecretSettings",
    "SettingsContext",
    "SettingsService",
    "GlobalConfigStore",
    "ProjectConfigStore",
]
