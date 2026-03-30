"""
src/infra/cli/project/commands.py — Project management commands.
"""
from __future__ import annotations
import click
from src.infra.cli.error_handler import catch_domain_errors, ok, warn, die


@click.group("project")
def project_group():
    """Manage the active project."""


@project_group.command("status")
def project_status():
    """Show the current active project."""
    from src.infra.settings import GlobalConfigStore
    from src.infra.container import AppContainer

    store = GlobalConfigStore()
    if not store.exists():
        die("No orchestrator config found.\n  Run: orchestrator init")

    app = AppContainer.from_env()
    project = app.ctx.machine.project_name
    if not project:
        die("No active project configured.\n  Run: orchestrator init\n  Or:  orchestrator project use <n>")

    click.echo(f"  Active project : {project}")
    click.echo(f"  Config file    : {store.config_path}")
    click.echo(f"  Redis URL      : {app.ctx.machine.redis_url}")

    project_home = app.ctx.machine.orchestrator_home / "projects" / project
    if project_home.exists():
        has_spec = (project_home / "project_spec.yaml").exists()
        status = "✓" if has_spec else "⚠  (no project_spec.yaml)"
        click.echo(f"  Project dir    : {project_home}  {status}")
    else:
        warn(f"Project directory does not exist: {project_home}")
        warn("Run: orchestrator init  to set up this project.")


@project_group.command("list")
def project_list():
    """List all projects found under ORCHESTRATOR_HOME."""
    from src.infra.settings import GlobalConfigStore
    from src.infra.container import AppContainer

    app = AppContainer.from_env()
    active = app.ctx.machine.project_name
    projects_root = app.ctx.machine.orchestrator_home / "projects"

    if not projects_root.exists():
        die(f"No projects directory found at {projects_root}.\n  Run: orchestrator init")

    dirs = sorted(p for p in projects_root.iterdir() if p.is_dir())
    if not dirs:
        click.echo("  No projects found.\n  Run: orchestrator init  to create one.")
        return

    click.echo(f"\n  Projects in {projects_root}:\n")
    for d in dirs:
        marker = "*" if d.name == active else " "
        flags = []
        if (d / "project_spec.yaml").exists(): flags.append("spec")
        if (d / "project.json").exists():       flags.append("settings")
        flag_str = f"  [{', '.join(flags)}]" if flags else "  [no spec]"
        click.echo(f"  {marker} {d.name}{flag_str}")

    store = GlobalConfigStore()
    if active:
        click.echo(f"\n  * = active project  (config: {store.config_path})")
    else:
        click.echo("\n  No active project set. Run: orchestrator project use <n>")


@project_group.command("use")
@click.argument("name")
def project_use(name: str):
    """Switch the active project to NAME."""
    from src.infra.settings import GlobalConfigStore
    from src.infra.container import AppContainer

    app = AppContainer.from_env()
    project_home = app.ctx.machine.orchestrator_home / "projects" / name

    if not project_home.exists():
        projects_root = app.ctx.machine.orchestrator_home / "projects"
        existing = sorted(p.name for p in projects_root.iterdir() if p.is_dir()) \
            if projects_root.exists() else []
        hint = f"  Available: {', '.join(existing)}" if existing else \
               "  No projects found. Run: orchestrator init"
        die(f"Project '{name}' does not exist.\n{hint}")

    GlobalConfigStore().update(project_name=name)
    ok(f"Active project set to '{name}'")
    click.echo(f"  Config: {GlobalConfigStore().config_path}")

    if not (project_home / "project_spec.yaml").exists():
        warn(f"Project '{name}' has no project_spec.yaml.\n  Run: orchestrator spec init to create one.")


@project_group.command("reset")
@click.option("--yes", "-y", is_flag=True, default=False)
@click.option("--keep-agents", is_flag=True, default=False)
@catch_domain_errors
def project_reset(yes: bool, keep_agents: bool):
    """Full project reset: delete all tasks, leases, git branches, and agents."""
    from src.infra.container import AppContainer
    app = AppContainer.from_env()

    if not yes:
        extra = "" if keep_agents else " + agents"
        click.confirm(
            f"  Reset project '{app.ctx.machine.project_name}' (tasks{extra} + git branches)?",
            abort=True,
        )

    result = app.project_reset_usecase.execute(keep_agents=keep_agents)

    if result.tasks_deleted:    click.echo(f"  ✓  Deleted {result.tasks_deleted} task(s)")
    if result.leases_released:  click.echo(f"  ✓  Released {result.leases_released} lease(s)")
    if result.branches_deleted: click.echo(f"  ✓  Deleted {result.branches_deleted} git branch(es)")
    if result.agents_removed:   click.echo(f"  ✓  Removed {result.agents_removed} agent(s) from registry")

    if result.had_errors:
        for e in result.errors: warn(e)
        warn(f"Reset completed with {len(result.errors)} error(s). See above.")
    else:
        ok(f"Project '{app.ctx.machine.project_name}' reset complete")
