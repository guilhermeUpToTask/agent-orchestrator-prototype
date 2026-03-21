"""
src/infra/cli/tasks/commands.py — Task management commands.

Commands:
  orchestrator tasks create   — create a new task
  orchestrator tasks list     — list all tasks
  orchestrator tasks retry    — force-requeue a task
  orchestrator tasks delete   — delete a single task record
  orchestrator tasks prune    — bulk-delete by status
"""
from __future__ import annotations

import os
import sys

import click

from src.infra.cli.error_handler import catch_domain_errors, die, ok, warn


@click.group("tasks")
def tasks_group():
    """Manage tasks."""


@tasks_group.command("create")
@click.option("--title",       required=True, help="Short title for the task")
@click.option("--description", required=True, help="Full description of what the agent must do")
@click.option("--feature-id",  default=None,  help="Feature group ID (auto-generated if omitted)")
@click.option("--capability",  required=True, help="Required agent capability, e.g. code:backend")
@click.option("--allow",   multiple=True, required=True, help="File allowed to modify (repeatable)")
@click.option("--test",    default=None,  help="Shell command to verify the result")
@click.option("--criteria",    multiple=True, help="Acceptance criteria lines (repeatable)")
@click.option("--depends-on",  multiple=True, help="Task IDs this task depends on (repeatable)")
@click.option("--max-retries", default=2,     help="Max retry attempts on failure")
@click.option("--min-version", default=">=1.0.0", help="Minimum agent version constraint")
@catch_domain_errors
def task_create(
    title: str,
    description: str,
    feature_id: str | None,
    capability: str,
    allow: tuple,
    test: str | None,
    criteria: tuple,
    depends_on: tuple,
    max_retries: int,
    min_version: str,
):
    """Create a task and emit task.created."""
    from src.infra.factory import build_task_creation_service

    from src.infra.config import config as app_config
    mode = app_config.mode
    if mode != "real":
        warn(
            f"AGENT_MODE is not 'real' (current: '{mode}').\n"
            "   Events will NOT reach Redis. "
            "Run with: AGENT_MODE=real orchestrator tasks create ..."
        )

    service = build_task_creation_service()
    task = service.create_task(
        title=title,
        description=description,
        feature_id=feature_id,
        capability=capability,
        files_allowed_to_modify=list(allow),
        test_command=test,
        acceptance_criteria=list(criteria),
        depends_on=list(depends_on),
        max_retries=max_retries,
        min_version=min_version,
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
    from src.infra.factory import build_task_repo

    tasks = build_task_repo().list_all()

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
    """
    Force-requeue a task without incrementing the retry counter.

    Works for any non-MERGED status. This is an operator override,
    not an automatic retry.
    """
    from src.infra.factory import build_task_retry_usecase

    result = build_task_retry_usecase().execute(task_id)
    ok(f"Task {task_id} requeued  ({result.previous_status.value} → requeued)")


@tasks_group.command("delete")
@click.argument("task_id")
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip confirmation prompt")
@catch_domain_errors
def task_delete(task_id: str, yes: bool):
    """
    Permanently delete a single task record.

    Does NOT clean up associated Git branches or Redis leases.
    Use  orchestrator project reset  for a full teardown.
    """
    from src.infra.factory import build_task_repo, build_task_delete_usecase

    task = build_task_repo().get(task_id)
    if task is None:
        die(f"Task not found: {task_id}")

    if not yes:
        click.confirm(
            f"  Delete task {task_id} (status: {task.status.value})?",
            abort=True,
        )

    build_task_delete_usecase().execute(task_id)
    ok(f"Task {task_id} deleted")


@tasks_group.command("prune")
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip confirmation prompt")
@click.option(
    "--status",
    multiple=True,
    default=[],
    help="Only prune tasks with this status (repeatable). Omit to prune ALL.",
)
@catch_domain_errors
def task_prune(yes: bool, status: tuple):
    """
    Bulk-delete task records, optionally filtered by status.

    Examples:
      orchestrator tasks prune                          # delete all
      orchestrator tasks prune --status failed          # only failed
      orchestrator tasks prune --status failed --status canceled
    """
    from src.infra.factory import build_task_repo, build_task_prune_usecase
    from src.domain import TaskStatus

    filter_statuses: set[TaskStatus] | None = None
    label = "ALL"

    if status:
        try:
            filter_statuses = {TaskStatus(s) for s in status}
            label = f"with status {'/'.join(status)}"
        except ValueError as exc:
            die(f"Invalid status value: {exc}")

    all_tasks = build_task_repo().list_all()
    targets = (
        all_tasks if filter_statuses is None
        else [t for t in all_tasks if t.status in filter_statuses]
    )

    if not targets:
        click.echo(f"  No tasks found ({label}).")
        return

    if not yes:
        click.confirm(f"  Delete {len(targets)} task(s) ({label})?", abort=True)

    result = build_task_prune_usecase().execute(filter_statuses=filter_statuses)
    ok(f"Deleted {result.count} task(s) ({label})")
