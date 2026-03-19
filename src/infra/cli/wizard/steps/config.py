"""
src/infra/cli/wizard/steps/config.py — Wizard Step 1: project configuration.
"""
from __future__ import annotations

from typing import Any

import click

from src.infra.config_manager import DEFAULTS, OrchestratorConfigManager


def collect_project_config(manager: OrchestratorConfigManager) -> dict[str, Any]:
    """
    Prompt the user for the three core config values and return them.
    Existing config values are used as defaults.
    """
    existing = manager.load()

    project_name: str = click.prompt(
        "  Project name",
        default=existing.get("project_name") or DEFAULTS["project_name"],
    )

    source_repo_url: str = click.prompt(
        "  Source repository URL  (blank → empty local repo)",
        default=existing.get("source_repo_url") or "",
    )

    redis_url: str = click.prompt(
        "  Redis URL",
        default=existing.get("redis_url") or DEFAULTS["redis_url"],
    )

    return {
        "project_name": project_name,
        "source_repo_url": source_repo_url.strip() or None,
        "redis_url": redis_url.strip(),
    }
