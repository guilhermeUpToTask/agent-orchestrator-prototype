"""
src/infra/cli/wizard/steps/config.py — Wizard Steps 1 & 3: configuration.

Step 1 (orchestrator-global):  project_name, redis_url
  → persisted in .orchestrator/config.json (per-machine, never in git)

Step 3 (project-scoped):       source_repo_url
  → persisted in ~/.orchestrator/projects/<n>/project.json (per-project)

This separation makes the boundary explicit to the user: the orchestrator
setup questions are about the machine; the project setup questions are about
what project is being worked on.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from src.infra.config_manager import DEFAULTS, OrchestratorConfigManager


def collect_orchestrator_config(manager: OrchestratorConfigManager) -> dict[str, Any]:
    """
    Step 1 — Prompt for orchestrator-global settings.

    Returns a dict suitable for OrchestratorConfigManager.save():
      project_name  — which project directory to use
      redis_url     — broker connection string

    source_repo_url is intentionally NOT collected here; it belongs to the
    project layer and is collected in collect_project_settings() below.
    """
    existing = manager.load()

    # DEFAULTS["project_name"] is intentionally None — no silent fallback.
    # If there is no existing project name the user must type one explicitly.
    existing_project = existing.get("project_name")
    project_name: str = click.prompt(
        "  Project name",
        default=existing_project if existing_project else None,
        prompt_suffix=" (required): " if not existing_project else ": ",
    )
    if not project_name or not project_name.strip():
        raise click.UsageError("Project name cannot be empty.")
    project_name = project_name.strip()

    redis_url: str = click.prompt(
        "  Redis URL",
        default=existing.get("redis_url") or DEFAULTS["redis_url"],
    )

    return {
        "project_name": project_name,
        "redis_url": redis_url.strip(),
    }


def collect_project_settings(project_name: str, orchestrator_home: Path) -> dict[str, Any]:
    """
    Step 3 — Prompt for project-scoped operational settings.

    Returns a dict suitable for ProjectSettingsManager.save():
      source_repo_url — upstream git repo to clone on first init (optional)

    Reads the existing project.json as defaults so re-running init is safe.
    """
    from src.infra.project_settings import ProjectSettingsManager

    project_home = orchestrator_home / "projects" / project_name
    manager = ProjectSettingsManager(project_home)
    existing = manager.load()

    source_repo_url: str = click.prompt(
        "  Source repository URL  (blank → empty local repo)",
        default=existing.source_repo_url or "",
    )

    return {
        "source_repo_url": source_repo_url.strip() or None,
    }


# ---------------------------------------------------------------------------
# Backward-compat shim — tests and external code still call collect_project_config
# ---------------------------------------------------------------------------

def collect_project_config(manager: OrchestratorConfigManager) -> dict[str, Any]:
    """
    Backward-compatible wrapper that returns all fields in a single dict.

    Combines orchestrator config + project settings into one dict so that
    existing callers (test_wizard.py, etc.) still work during the transition.
    The wizard __init__.py uses the split functions directly.

    Deprecated: prefer collect_orchestrator_config + collect_project_settings.
    """
    orch_data = collect_orchestrator_config(manager)

    # source_repo_url is now a project setting, but we still prompt for it
    # here for backward compat — the wizard will save it to project.json
    source_repo_url: str = click.prompt(
        "  Source repository URL  (blank → empty local repo)",
        default=manager.load().get("source_repo_url") or "",
    )

    return {
        **orch_data,
        "source_repo_url": source_repo_url.strip() or None,
    }
