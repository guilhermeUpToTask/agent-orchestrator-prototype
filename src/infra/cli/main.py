"""
src/infra/cli/main.py — CLI root group and entry point.

Usage:
  python -m src.infra.cli.main <command group> <command> [options]

Command groups:
  init              Run the setup wizard
  system start      Boot all daemons
  system task-manager / worker / reconciler
  tasks create / list / retry / delete / prune
  agents create / list / delete / edit
  project reset
"""
from __future__ import annotations

import sys

import click
import structlog

structlog.configure(processors=[structlog.dev.ConsoleRenderer()])

from src.infra.cli.system.commands  import system_group
from src.infra.cli.tasks.commands   import tasks_group
from src.infra.cli.agents.commands  import agents_group
from src.infra.cli.project.commands import project_group
from src.infra.cli.goals.commands   import goals_group
from src.infra.cli.wizard import run_wizard


@click.group()
def cli():
    """Agent Orchestrator — coordinate CLI-based coding agents."""


# ---------------------------------------------------------------------------
# init / wizard
# ---------------------------------------------------------------------------

@cli.command("init")
@click.option(
    "--defaults",
    is_flag=True,
    default=False,
    help="Write a config.json with defaults (skips interactive prompts).",
)
def init_wizard(defaults: bool):
    """
    Run the setup wizard to create .orchestrator/config.json.

    Collects project_name, source_repo_url, and redis_url, verifies that
    Redis, git, and at least one agent runtime are available, then writes
    the config file.  If the agent registry is empty you will be offered
    a chance to register a first agent interactively.

    Use --defaults to generate a non-interactive config (useful in CI).
    """
    from src.infra.config_manager import OrchestratorConfigManager

    if defaults:
        manager = OrchestratorConfigManager()
        data    = manager.generate_defaults()
        click.echo(f"✓  Default config written → {manager.config_path}")
        for k, v in data.items():
            click.echo(f"   {k}: {v}")
        return

    success = run_wizard()
    sys.exit(0 if success else 1)


# ---------------------------------------------------------------------------
# Register command groups
# ---------------------------------------------------------------------------

cli.add_command(system_group)
cli.add_command(tasks_group)
cli.add_command(agents_group)
cli.add_command(project_group)
cli.add_command(goals_group)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cli()
