"""
src/infra/settings/defaults.py — Default values and schema constants.
"""

from __future__ import annotations
from pathlib import Path

# ---------------------------------------------------------------------------
# MachineSettings defaults
# ---------------------------------------------------------------------------

MACHINE_DEFAULTS: dict = {
    "mode": "dry-run",
    "redis_url": "redis://localhost:6379/0",
    "task_timeout": 600,
    "orchestrator_home": Path.home() / ".orchestrator",
    "project_name": None,
}

# Keys persisted by the wizard in config.json — subset of MachineSettings.
# Schema metadata lives here, not on the dataclass.
MACHINE_PERSISTABLE_KEYS: frozenset[str] = frozenset({"project_name", "redis_url", "task_timeout"})

# Backward-compat alias used by a few test files
MACHINE_MANAGED_KEYS = MACHINE_PERSISTABLE_KEYS

# ---------------------------------------------------------------------------
# ProjectSettings defaults
# ---------------------------------------------------------------------------

PROJECT_DEFAULTS: dict = {
    "source_repo_url": None,
    "github_owner": None,
    "github_repo": None,
    "github_base_branch": "main",
}

PROJECT_MANAGED_KEYS: frozenset[str] = frozenset(
    {"source_repo_url", "github_owner", "github_repo", "github_base_branch"}
)

# ---------------------------------------------------------------------------
# Config file names
# ---------------------------------------------------------------------------

GLOBAL_CONFIG_FILENAME = "config.json"
PROJECT_CONFIG_FILENAME = "project.json"
