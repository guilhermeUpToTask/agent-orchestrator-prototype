"""
src/cli.py — Command-line entry point.

Usage:
  python -m src.cli start             # Boot full system from registry.json
  python -m src.cli task-manager      # Start task manager event loop
  python -m src.cli worker            # Start worker event loop (no RQ)
  python -m src.cli reconciler        # Start reconciler loop
  python -m src.cli create-task       # Create a task from YAML file
  python -m src.cli list-tasks        # Print task statuses
  python -m src.cli register-agent    # Register an agent in the registry
"""
from __future__ import annotations

import itertools
import os
import sys
import time
from pathlib import Path

import click
import structlog

structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
log = structlog.get_logger()

_AGENT_ID = os.getenv("AGENT_ID", "agent-worker-001")
_HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "30"))


@click.group()
def cli():
    """Agent Orchestrator CLI."""
    pass


# ---------------------------------------------------------------------------
# System boot — starts all processes from registry
# ---------------------------------------------------------------------------

@cli.command("start")
@click.option("--reconciler-interval", default=30, help="Reconciler interval in seconds")
@click.option("--heartbeat-timeout", default=30, help="Seconds to wait for worker heartbeats")
def start_system(reconciler_interval: int, heartbeat_timeout: int):
    """
    Boot the full system: task-manager + all active workers + reconciler.
    Active workers are read from registry.json (active: true).
    Boot order is enforced: workers must heartbeat before reconciler starts.
    """
    import subprocess

    from src.infra.factory import build_agent_registry

    registry = build_agent_registry()
    active_agents = [a for a in registry.list_agents() if a.active]

    if not active_agents:
        click.echo("⚠  No active agents found in registry. Register agents first.", err=True)
        sys.exit(1)

    env = {**os.environ, "AGENT_MODE": "real"}
    procs = []

    try:
        # 1. Start TaskManager
        p = subprocess.Popen(["python", "-m", "src.cli", "task-manager"], env=env)
        procs.append(("task-manager", p))
        click.echo("✓ TaskManager started")

        # 2. Start each active worker
        for agent in active_agents:
            worker_env = {**env, "AGENT_ID": agent.agent_id}
            p = subprocess.Popen(["python", "-m", "src.cli", "worker"], env=worker_env)
            procs.append((agent.agent_id, p))
            click.echo(f"✓ Worker started: {agent.agent_id}")

        # 3. Wait for all workers to heartbeat before starting reconciler.
        #    This prevents the reconciler from re-emitting CREATED tasks
        #    before anyone is listening for task.assigned.
        click.echo(f"\nWaiting for workers to heartbeat (timeout={heartbeat_timeout}s)...")
        _wait_for_heartbeats(registry, active_agents, timeout=heartbeat_timeout)

        # 4. Start Reconciler last
        p = subprocess.Popen(["python", "-m", "src.cli", "reconciler",
                               f"--interval={reconciler_interval}"], env=env)
        procs.append(("reconciler", p))
        click.echo("✓ Reconciler started\n")
        click.echo("System ready. Ctrl+C to stop all.\n")

        # Block until any child exits (unexpected) or Ctrl+C
        while True:
            for name, p in procs:
                if p.poll() is not None:
                    click.echo(f"⚠  Process '{name}' exited with code {p.returncode}", err=True)
            time.sleep(2)

    except KeyboardInterrupt:
        click.echo("\nShutting down...")
    finally:
        for name, p in procs:
            p.terminate()
        for name, p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        click.echo("All processes stopped.")


def _wait_for_heartbeats(registry, agents, timeout: int = 30) -> None:
    from src.core.services import _is_alive
    deadline = time.time() + timeout
    while time.time() < deadline:
        # Re-read registry on each check so we see fresh heartbeats
        fresh_agents = [registry.get(a.agent_id) for a in agents]
        if all(a and _is_alive(a) for a in fresh_agents):
            click.echo("✓ All workers alive\n")
            return
        time.sleep(1)
    click.echo("⚠  Some workers did not heartbeat in time — proceeding anyway", err=True)


# ---------------------------------------------------------------------------
# Task Manager event loop
# ---------------------------------------------------------------------------

@cli.command("task-manager")
def run_task_manager():
    """Subscribe to task events and assign / unblock tasks."""
    from src.infra.factory import build_task_manager_handler, build_event_port

    handler = build_task_manager_handler()
    events = build_event_port()

    click.echo("Task Manager started — listening for task.created / task.requeued / task.completed")

    # subscribe_many reads all three streams in a single XREADGROUP call.
    # Using itertools.chain() on blocking generators would silently starve
    # task.requeued and task.completed — the first stream blocks forever.
    for event in events.subscribe_many(
        ["task.created", "task.requeued", "task.completed"],
        group="task-manager",
        consumer="tm-1",
    ):
        task_id = event.payload.get("task_id")
        if not task_id:
            continue
        if event.type == "task.created":
            handler.handle_task_created(task_id)
        elif event.type == "task.requeued":
            handler.handle_task_requeued(task_id)
        elif event.type == "task.completed":
            handler.handle_task_completed(task_id)


# ---------------------------------------------------------------------------
# Worker event loop (replaces RQ worker)
# ---------------------------------------------------------------------------

@cli.command("worker")
def run_worker():
    """
    Subscribe to task.assigned events and process tasks assigned to this agent.
    Uses Redis Streams consumer groups — each task.assigned is delivered to
    exactly one worker even when multiple workers are running concurrently.
    """
    import threading
    from src.infra.factory import build_event_port, build_worker_handler, build_agent_registry

    agent_id = _AGENT_ID
    events = build_event_port()
    registry = build_agent_registry()
    handler = build_worker_handler()

    click.echo(f"Worker {agent_id} started — listening for task.assigned")

    # Send heartbeat immediately so the system knows we're alive,
    # then keep updating it in the background.
    registry.heartbeat(agent_id)
    _start_heartbeat_thread(registry, agent_id)

    for event in events.subscribe("task.assigned", group="workers", consumer=agent_id):
        assigned_to = event.payload.get("agent_id")
        task_id = event.payload.get("task_id")
        project_id = event.payload.get("project_id", "")

        # Only process tasks assigned to this specific agent.
        # With consumer groups each message goes to one worker, but the
        # task manager targets a specific agent — skip if it isn't us.
        if assigned_to != agent_id:
            log.info("worker.skip_not_mine", task_id=task_id, assigned_to=assigned_to)
            continue

        handler.process(task_id=task_id, project_id=project_id)


def _start_heartbeat_thread(registry, agent_id: str) -> None:
    """Send a heartbeat every HEARTBEAT_INTERVAL seconds in the background."""
    import threading

    def _beat():
        while True:
            time.sleep(_HEARTBEAT_INTERVAL)
            try:
                registry.heartbeat(agent_id)
            except Exception:
                pass  # don't crash the worker on a registry write failure

    t = threading.Thread(target=_beat, daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# Reconciler
# ---------------------------------------------------------------------------

@cli.command("reconciler")
@click.option("--interval", default=30, help="Seconds between reconcile passes")
def run_reconciler(interval: int):
    """Run the reconciler loop (detects expired leases, dead agents, stuck tasks)."""
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
    """
    Create a task from a YAML file and emit task.created.
    The YAML is the source of truth; the event triggers the task manager.
    If the event is lost (e.g. crash), the reconciler will re-emit it.
    """
    import yaml
    from src.core.models import TaskAggregate, DomainEvent
    from src.infra.factory import build_task_repo, build_event_port

    mode = os.getenv("AGENT_MODE", "dry-run")
    if mode != "real":
        click.echo(
            "⚠  AGENT_MODE is not 'real' (current: '{}').\n"
            "   Events will NOT reach Redis. "
            "Run with: AGENT_MODE=real python -m src.cli create-task ...".format(mode),
            err=True,
        )

    data = yaml.safe_load(Path(yaml_file).read_text())
    task = TaskAggregate.model_validate(data)

    repo = build_task_repo()
    repo.save(task)                     # 1. Persist first (source of truth)

    events = build_event_port()
    events.publish(DomainEvent(         # 2. Emit event to trigger task manager
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
        deps = f" [depends: {','.join(t.depends_on)}]" if t.depends_on else ""
        click.echo(f"{t.task_id:<20} {t.status.value:<15} {agent:<25} {t.state_version}{deps}")


# ---------------------------------------------------------------------------
# Register agent
# ---------------------------------------------------------------------------

@cli.command("register-agent")
@click.option("--agent-id", required=True)
@click.option("--name", required=True)
@click.option("--capabilities", required=True, help="Comma-separated")
@click.option("--version", default="1.0.0")
@click.option("--active/--inactive", default=True, help="Whether to include in boot and scheduling")
def register_agent(agent_id: str, name: str, capabilities: str, version: str, active: bool):
    """Register a new agent in the registry."""
    from src.core.models import AgentProps
    from src.infra.factory import build_agent_registry

    agent = AgentProps(
        agent_id=agent_id,
        name=name,
        capabilities=[c.strip() for c in capabilities.split(",")],
        version=version,
        active=active,
    )
    registry = build_agent_registry()
    registry.register(agent)
    status = "active" if active else "inactive"
    click.echo(f"✓ Agent registered: {agent_id} ({status})")


if __name__ == "__main__":
    cli()