"""
src/infra/cli/tasks/commands.py — Task management commands.
"""
from __future__ import annotations

import click
from src.infra.cli.error_handler import catch_domain_errors, die, ok, warn


@click.group("tasks")
def tasks_group():
    """Manage tasks."""


@tasks_group.command("create")
@click.option("--title",       required=True)
@click.option("--description", required=True)
@click.option("--feature-id",  default=None)
@click.option("--capability",  required=True)
@click.option("--allow",   multiple=True, required=True)
@click.option("--test",    default=None)
@click.option("--criteria",    multiple=True)
@click.option("--depends-on",  multiple=True)
@click.option("--max-retries", default=2)
@click.option("--min-version", default=">=1.0.0")
@catch_domain_errors
def task_create(title, description, feature_id, capability, allow,
                test, criteria, depends_on, max_retries, min_version):
    """Create a task and emit task.created."""
    from src.infra.container import AppContainer
    app = AppContainer.from_env()
    if app.ctx.mode != "real":
        warn(f"AGENT_MODE is not 'real' (current: '{app.ctx.mode}'). Events will NOT reach Redis.")

    task = app.task_creation_service.create_task(
        title=title, description=description, feature_id=feature_id,
        capability=capability, files_allowed_to_modify=list(allow),
        test_command=test, acceptance_criteria=list(criteria),
        depends_on=list(depends_on), max_retries=max_retries, min_version=min_version,
    )
    ok(f"Task created: {task.task_id}")
    click.echo(f"   title:      {title}")
    click.echo(f"   feature:    {task.feature_id}")
    click.echo(f"   capability: {capability}")
    click.echo(f"   files:      {', '.join(allow)}")
    if test:
        click.echo(f"   test:       {test}")
    if depends_on:
        click.echo(f"   depends on: {', '.join(depends_on)}")


@tasks_group.command("list")
@catch_domain_errors
def task_list():
    """Print current status of all tasks."""
    from src.infra.container import AppContainer
    app = AppContainer.from_env()
    tasks = app.task_repo.list_all()

    if not tasks:
        click.echo("No tasks found.")
        return

    click.echo(f"\n{'TASK ID':<20} {'STATUS':<15} {'AGENT':<25} {'VERSION'}")
    click.echo("-" * 75)
    for t in tasks:
        agent = t.assignment.agent_id if t.assignment else "-"
        deps  = f" [depends: {','.join(t.depends_on)}]" if t.depends_on else ""
        click.echo(f"{t.task_id:<20} {t.status.value:<15} {agent:<25} {t.state_version}{deps}")


@tasks_group.command("retry")
@click.argument("task_id")
@catch_domain_errors
def task_retry(task_id: str):
    """Force-requeue a task without incrementing the retry counter."""
    from src.infra.container import AppContainer
    result = AppContainer.from_env().task_retry_usecase.execute(task_id)
    ok(f"Task {task_id} requeued  ({result.previous_status.value} → requeued)")


@tasks_group.command("delete")
@click.argument("task_id")
@click.option("--yes", "-y", is_flag=True, default=False)
@catch_domain_errors
def task_delete(task_id: str, yes: bool):
    """Permanently delete a single task record."""
    from src.infra.container import AppContainer
    app = AppContainer.from_env()
    task = app.task_repo.get(task_id)
    if task is None:
        die(f"Task not found: {task_id}")
    if not yes:
        click.confirm(f"  Delete task {task_id} (status: {task.status.value})?", abort=True)
    app.task_delete_usecase.execute(task_id)
    ok(f"Task {task_id} deleted")


@tasks_group.command("prune")
@click.option("--yes", "-y", is_flag=True, default=False)
@click.option("--status", multiple=True, default=[])
@catch_domain_errors
def task_prune(yes: bool, status: tuple):
    """Bulk-delete task records, optionally filtered by status."""
    from src.infra.container import AppContainer
    from src.domain import TaskStatus

    app = AppContainer.from_env()
    filter_statuses = None
    label = "ALL"

    if status:
        try:
            filter_statuses = {TaskStatus(s) for s in status}
            label = f"with status {'/'.join(status)}"
        except ValueError as exc:
            die(f"Invalid status value: {exc}")

    all_tasks = app.task_repo.list_all()
    targets = (all_tasks if filter_statuses is None
               else [t for t in all_tasks if t.status in filter_statuses])

    if not targets:
        click.echo(f"  No tasks found ({label}).")
        return

    if not yes:
        click.confirm(f"  Delete {len(targets)} task(s) ({label})?", abort=True)

    result = app.task_prune_usecase.execute(filter_statuses=filter_statuses)
    ok(f"Deleted {result.count} task(s) ({label})")
