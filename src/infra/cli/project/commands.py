"""
src/infra/cli/project/commands.py — Project management commands.

Commands:
  orchestrator project status        — show the current active project
  orchestrator project list          — list all configured projects
  orchestrator project use <name>    — switch the active project
  orchestrator project reset         — full project wipe
"""
from __future__ import annotations

import click

from src.infra.cli.error_handler import catch_domain_errors, ok, warn, die


@click.group("project")
def project_group():
    """Manage the active project."""


@project_group.command("status")
def project_status():
    """
    Show the current active project.

    Reads from ORCHESTRATOR_HOME/config.json — the value set by
    `orchestrator init` or `orchestrator project use`.
    """
    from src.infra.config_manager import OrchestratorConfigManager

    mgr = OrchestratorConfigManager()

    if not mgr.exists():
        die(
            "No orchestrator config found.\n"
            "  Run: orchestrator init"
        )

    data = mgr.load()
    project = data.get("project_name")

    if not project:
        die(
            "No active project configured.\n"
            "  Run: orchestrator init\n"
            "  Or:  orchestrator project use <name>"
        )

    click.echo(f"  Active project : {project}")
    click.echo(f"  Config file    : {mgr.config_path}")
    click.echo(f"  Redis URL      : {data.get('redis_url', '(not set)')}")

    from src.infra.config import OrchestratorConfig
    cfg = OrchestratorConfig()
    project_home = cfg.orchestrator_home / "projects" / project
    if project_home.exists():
        has_spec = (project_home / "project_spec.yaml").exists()
        status = "✓" if has_spec else "⚠  (no project_spec.yaml)"
        click.echo(f"  Project dir    : {project_home}  {status}")
    else:
        warn(f"Project directory does not exist: {project_home}")
        warn("Run: orchestrator init  to set up this project.")


@project_group.command("list")
def project_list():
    """
    List all projects found under ORCHESTRATOR_HOME.

    The active project (from config.json) is marked with *.
    """
    from src.infra.config_manager import OrchestratorConfigManager
    from src.infra.config import OrchestratorConfig

    mgr = OrchestratorConfigManager()
    data = mgr.load()
    active = data.get("project_name")

    cfg = OrchestratorConfig()
    projects_root = cfg.orchestrator_home / "projects"

    if not projects_root.exists():
        die(
            f"No projects directory found at {projects_root}.\n"
            "  Run: orchestrator init"
        )

    dirs = sorted(p for p in projects_root.iterdir() if p.is_dir())

    if not dirs:
        click.echo("  No projects found.")
        click.echo("  Run: orchestrator init  to create one.")
        return

    click.echo(f"\n  Projects in {projects_root}:\n")
    for d in dirs:
        marker = "*" if d.name == active else " "
        has_spec = (d / "project_spec.yaml").exists()
        has_settings = (d / "project.json").exists()
        flags = []
        if has_spec:
            flags.append("spec")
        if has_settings:
            flags.append("settings")
        flag_str = f"  [{', '.join(flags)}]" if flags else "  [no spec]"
        click.echo(f"  {marker} {d.name}{flag_str}")

    if active:
        click.echo(f"\n  * = active project  (config: {mgr.config_path})")
    else:
        click.echo("\n  No active project set. Run: orchestrator project use <name>")


@project_group.command("use")
@click.argument("name")
def project_use(name: str):
    """
    Switch the active project to NAME.

    Updates ORCHESTRATOR_HOME/config.json so all subsequent commands
    (plan init, goals, tasks, etc.) operate on this project.

    NAME must already exist under ORCHESTRATOR_HOME/projects/.
    Run `orchestrator project list` to see available projects, or
    `orchestrator init` to create a new one.
    """
    from src.infra.config_manager import OrchestratorConfigManager
    from src.infra.config import OrchestratorConfig

    cfg = OrchestratorConfig()
    project_home = cfg.orchestrator_home / "projects" / name

    if not project_home.exists():
        projects_root = cfg.orchestrator_home / "projects"
        existing = sorted(p.name for p in projects_root.iterdir() if p.is_dir()) \
            if projects_root.exists() else []
        hint = f"  Available: {', '.join(existing)}" if existing else \
               "  No projects found. Run: orchestrator init"
        die(
            f"Project \'{name}\' does not exist.\n"
            f"{hint}"
        )

    mgr = OrchestratorConfigManager()
    mgr.update(project_name=name)

    ok(f"Active project set to \'{name}\'")
    click.echo(f"  Config: {mgr.config_path}")

    if not (project_home / "project_spec.yaml").exists():
        warn(
            f"Project \'{name}\' has no project_spec.yaml.\n"
            "  Run: orchestrator spec init   to create one."
        )

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
