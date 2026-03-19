"""
src/infra/cli/project/commands.py — Project management commands.

Commands:
  orchestrator project reset  — full project wipe
"""
from __future__ import annotations

import click

from src.infra.cli.error_handler import catch_domain_errors, ok, warn


@click.group("project")
def project_group():
    """Manage the active project."""


@project_group.command("reset")
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip confirmation prompt")
@click.option(
    "--keep-agents",
    is_flag=True,
    default=False,
    help="Keep the agent registry intact (only delete tasks and branches)",
)
@catch_domain_errors
def project_reset(yes: bool, keep_agents: bool):
    """
    Full project reset: delete all tasks, leases, git branches, and agents.

    What gets deleted:
      • All task YAML files
      • All Redis leases
      • All remote Git branches matching task-<id> naming
      • The agent registry  (unless --keep-agents is given)

    Example:
      orchestrator project reset
      orchestrator project reset --keep-agents
    """
    from src.infra.factory import build_project_reset_usecase
    from src.infra.config import config as app_config

    if not yes:
        extra = "" if keep_agents else " + agents"
        click.confirm(
            f"  Reset project '{app_config.project_name}' (tasks{extra} + git branches)?",
            abort=True,
        )

    result = build_project_reset_usecase().execute(keep_agents=keep_agents)

    if result.tasks_deleted:
        click.echo(f"  ✓  Deleted {result.tasks_deleted} task(s)")
    if result.leases_released:
        click.echo(f"  ✓  Released {result.leases_released} lease(s)")
    if result.branches_deleted:
        click.echo(f"  ✓  Deleted {result.branches_deleted} git branch(es)")
    if result.agents_removed:
        click.echo(f"  ✓  Removed {result.agents_removed} agent(s) from registry")

    if result.had_errors:
        for e in result.errors:
            warn(e)
        warn(f"Reset completed with {len(result.errors)} error(s). See above.")
    else:
        ok(f"Project '{app_config.project_name}' reset complete")
