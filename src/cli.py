"""
src/cli.py — Command-line entry point.

Usage:
  python -m src.cli task-manager       # Start task manager event loop
  python -m src.cli worker             # Start RQ worker
  python -m src.cli reconciler         # Start reconciler loop
  python -m src.cli create-task        # Create a task from YAML file
  python -m src.cli list-tasks         # Print task statuses
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import click
import structlog

structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
log = structlog.get_logger()


@click.group()
def cli():
    """Agent Orchestrator CLI."""
    pass


# ---------------------------------------------------------------------------
# Task Manager event loop
# ---------------------------------------------------------------------------

@cli.command("task-manager")
def run_task_manager():
    """Subscribe to task.created/requeued events and assign tasks."""
    from src.infra.factory import (
        build_task_manager_handler, build_event_port
    )
    handler = build_task_manager_handler()
    events = build_event_port()

    click.echo("Task Manager started — listening for task.created / task.requeued")

    import itertools
    created = events.subscribe("task.created")
    requeued = events.subscribe("task.requeued")

    for event in itertools.chain(created, requeued):
        task_id = event.payload.get("task_id")
        if not task_id:
            continue
        if event.type == "task.created":
            handler.handle_task_created(task_id)
        else:
            handler.handle_task_requeued(task_id)


# ---------------------------------------------------------------------------
# Reconciler
# ---------------------------------------------------------------------------

@cli.command("reconciler")
@click.option("--interval", default=30, help="Seconds between reconcile passes")
def run_reconciler(interval: int):
    """Run the reconciler loop (detects expired leases)."""
    from src.infra.factory import build_reconciler
    reconciler = build_reconciler(interval_seconds=interval)
    click.echo(f"Reconciler started — interval={interval}s")
    reconciler.run_forever()


# ---------------------------------------------------------------------------
# Create task
# ---------------------------------------------------------------------------

@cli.command("create-task")
@click.argument("yaml_file", type=click.Path(exists=True))
def create_task(yaml_file: str):
    """Create a task from a YAML file and emit task.created event."""
    import yaml
    from src.core.models import TaskAggregate, DomainEvent
    from src.infra.factory import build_task_repo, build_event_port

    data = yaml.safe_load(Path(yaml_file).read_text())
    task = TaskAggregate.model_validate(data)

    repo = build_task_repo()
    repo.save(task)

    events = build_event_port()
    events.publish(DomainEvent(
        type="task.created",
        producer="cli",
        payload={"task_id": task.task_id},
    ))
    click.echo(f"✓ Task created: {task.task_id}")


# ---------------------------------------------------------------------------
# List tasks
# ---------------------------------------------------------------------------

@cli.command("list-tasks")
def list_tasks():
    """Print current status of all tasks."""
    from src.infra.factory import build_task_repo
    repo = build_task_repo()
    tasks = repo.list_all()

    if not tasks:
        click.echo("No tasks found.")
        return

    click.echo(f"\n{'TASK ID':<20} {'STATUS':<15} {'AGENT':<25} {'VERSION'}")
    click.echo("-" * 75)
    for t in tasks:
        agent = t.assignment.agent_id if t.assignment else "-"
        click.echo(f"{t.task_id:<20} {t.status.value:<15} {agent:<25} {t.state_version}")


# ---------------------------------------------------------------------------
# Register agent
# ---------------------------------------------------------------------------

@cli.command("register-agent")
@click.option("--agent-id", required=True)
@click.option("--name", required=True)
@click.option("--capabilities", required=True, help="Comma-separated")
@click.option("--version", default="1.0.0")
def register_agent(agent_id: str, name: str, capabilities: str, version: str):
    """Register a new agent in the registry."""
    from src.core.models import AgentProps
    from src.infra.factory import build_agent_registry

    agent = AgentProps(
        agent_id=agent_id,
        name=name,
        capabilities=[c.strip() for c in capabilities.split(",")],
        version=version,
    )
    registry = build_agent_registry()
    registry.register(agent)
    click.echo(f"✓ Agent registered: {agent_id}")


if __name__ == "__main__":
    cli()
