"""
src/infra/cli/wizard/ — Interactive setup wizard.

Public entry point:
    from src.infra.cli.wizard import run_wizard
    success = run_wizard()

Steps:
    steps/config.py   — collect project_name, redis_url, source_repo_url
    steps/deps.py     — verify redis, git, at least one runtime
    steps/registry.py — inspect registry, optionally register first agent
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import click

from src.infra.cli.wizard.steps.config   import collect_project_config
from src.infra.cli.wizard.steps.deps     import check_and_report
from src.infra.cli.wizard.steps.registry import setup_registry
from src.infra.config_manager import OrchestratorConfigManager


def run_wizard(
    cwd: Path | None = None,
    *,
    registry_factory: Callable | None = None,
) -> bool:
    """
    Run the interactive setup wizard.
    Returns True on success, False on failure/abort.
    """
    manager = OrchestratorConfigManager(cwd)

    _print_banner()

    click.echo(_section("Step 1 of 3 — Project Configuration"))
    config_data = collect_project_config(manager)
    click.echo()

    click.echo(_section("Step 2 of 3 — Dependency Check"))
    if not check_and_report(config_data["redis_url"]):
        click.echo("\n✗  Fix the issues above then re-run:  orchestrator init\n", err=True)
        return False

    manager.save(config_data)
    click.echo(f"\n  ✓ Config written → {manager.config_path}")

    click.echo(_section("Step 3 of 3 — Agent Registry"))
    setup_registry(config_data, registry_factory)

    click.echo("\n✓  Setup complete!  Run:  orchestrator start\n")
    return True


def _print_banner() -> None:
    click.echo()
    click.echo("┌──────────────────────────────────────────────────┐")
    click.echo("│     Agent Orchestrator  —  Setup Wizard          │")
    click.echo("└──────────────────────────────────────────────────┘")
    click.echo()


def _section(title: str) -> str:
    line = "─" * (len(title) + 4)
    return f"\n  ── {title} {line}"
