"""
src/infra/cli/main.py — CLI root group and entry point.
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
from src.infra.cli.spec.commands    import spec_group
from src.infra.cli.plan.commands    import plan_group
from src.infra.cli.wizard import run_wizard


@click.group()
def cli():
    """Agent Orchestrator — coordinate CLI-based coding agents."""


@cli.command("init")
@click.option("--defaults", is_flag=True, default=False,
              help="Write a config.json with defaults (skips interactive prompts).")
def init_wizard(defaults: bool):
    """
    Run the setup wizard to create .orchestrator/config.json.

    Use --defaults to generate a non-interactive config (useful in CI).
    """
    from src.infra.settings import GlobalConfigStore

    if defaults:
        store = GlobalConfigStore()
        data  = store.generate_defaults()
        click.echo(f"✓  Default config written → {store.config_path}")
        for k, v in data.items():
            click.echo(f"   {k}: {v}")
        return

    success = run_wizard()
    sys.exit(0 if success else 1)


cli.add_command(system_group)
cli.add_command(tasks_group)
cli.add_command(agents_group)
cli.add_command(project_group)
cli.add_command(goals_group)
cli.add_command(spec_group)
cli.add_command(plan_group)

if __name__ == "__main__":
    cli()
