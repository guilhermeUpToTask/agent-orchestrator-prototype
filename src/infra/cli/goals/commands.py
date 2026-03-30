"""
src/infra/cli/goals/commands.py — Goal management commands.
"""
from __future__ import annotations
import click
from src.infra.cli.error_handler import catch_domain_errors, die, info, ok, warn


@click.group("goals")
def goals_group():
    """Manage goals."""


@goals_group.command("init")
@click.argument("goal_file", type=click.Path(exists=True, dir_okay=False))
@catch_domain_errors
def goal_init(goal_file: str):
    """Parse GOAL_FILE, create all tasks, and set up the goal branch."""
    from src.infra.goal_file import load_goal_file
    from src.infra.container import AppContainer

    app = AppContainer.from_env()
    if app.ctx.mode != "real":
        warn(
            f"AGENT_MODE is not 'real' (current: '{app.ctx.mode}').\n"
            "   Events will NOT reach Redis. "
            "Run with: AGENT_MODE=real orchestrator goals init <file>"
        )

    spec = load_goal_file(goal_file)
    goal = app.goal_init_usecase.execute(spec)

    ok(f"Goal initialized: {goal.goal_id}")
    info(f"Name:        {goal.name}")
    info(f"Branch:      {goal.branch}")
    info(f"Tasks:       {len(goal.tasks)}")
    for tid, summary in goal.tasks.items():
        deps = f"  (depends: {', '.join(summary.depends_on)})" if summary.depends_on else ""
        info(f"  • {tid}{deps}")


@goals_group.command("run")
@catch_domain_errors
def goal_run():
    """Start the TaskGraphOrchestrator event loop."""
    from src.infra.container import AppContainer

    app = AppContainer.from_env()
    if app.ctx.mode != "real":
        warn(
            f"AGENT_MODE is not 'real' (current: '{app.ctx.mode}').\n"
            "   The orchestrator needs Redis to receive events."
        )

    info("TaskGraphOrchestrator starting. Press Ctrl+C to stop.")
    app.task_graph_orchestrator.run_forever()


@goals_group.command("status")
@click.argument("goal_id", required=False)
@catch_domain_errors
def goal_status(goal_id: str | None):
    """Show goal progress and per-task status."""
    from src.infra.container import AppContainer

    app = AppContainer.from_env()
    goals = app.goal_repo.list_goals()

    if not goals:
        click.echo("  No goals found.")
        return

    if goal_id:
        goals = [g for g in goals if g.goal_id == goal_id]
        if not goals:
            die(f"Goal '{goal_id}' not found.")

    for goal in goals:
        click.echo(f"\n  Goal: {goal.goal_id}  [{goal.status}]")
        click.echo(f"  Name: {goal.name}")
        if hasattr(goal, "branch"):
            click.echo(f"  Branch: {goal.branch}")
        for tid, summary in goal.tasks.items():
            click.echo(f"    • {tid}  [{summary.status}]")


@goals_group.command("finalize")
@click.argument("goal_id")
@catch_domain_errors
def goal_finalize(goal_id: str):
    """Finalize a completed goal."""
    from src.infra.container import AppContainer

    app = AppContainer.from_env()
    result = app.goal_finalize_usecase.execute(goal_id)
    ok(f"Goal '{goal_id}' finalized.")
    if hasattr(result, "merged_tasks"):
        info(f"Merged tasks: {result.merged_tasks}")
