"""
src/infra/cli/system/commands.py — System daemon commands.

Commands:
  orchestrator system start          — boot all daemons
  orchestrator system task-manager   — run the task manager event loop
  orchestrator system worker         — run a worker event loop
  orchestrator system reconciler     — run the reconciler loop
"""
from __future__ import annotations

import os
import sys
import time

import click
import structlog

from src.infra.cli.error_handler import die, warn, ok

log = structlog.get_logger(__name__)

# AGENT_ID and HEARTBEAT_INTERVAL_SECONDS are resolved lazily from OrchestratorConfig
# inside each command that needs them — no module-level env reads.
_HEARTBEAT_INTERVAL = 30  # seconds between registry heartbeat pings


@click.group("system")
def system_group():
    """System daemon management."""


@system_group.command("start")
@click.option("--reconciler-interval", default=60,  help="Reconciler poll interval (seconds)")
@click.option("--reconciler-stuck-age", default=120, help="Stuck-task threshold (seconds)")
@click.option("--heartbeat-timeout",   default=30,  help="Worker heartbeat wait (seconds)")
@click.option(
    "--skip-dep-check",
    is_flag=True,
    default=False,
    help="Skip dependency verification",
)
def system_start(
    reconciler_interval: int,
    reconciler_stuck_age: int,
    heartbeat_timeout: int,
    skip_dep_check: bool,
):
    """
    Boot the full system: task-manager + workers + reconciler.

    Active workers are read from the agent registry (active: true).
    Boot order is enforced: workers must heartbeat before reconciler starts.
    """
    import subprocess
    from src.infra.config_manager import OrchestratorConfigManager

    manager = OrchestratorConfigManager()
    if not manager.exists():
        manager.generate_defaults()
        click.echo(
            "  ℹ  No .orchestrator/config.json found — generated with defaults.\n"
            "     Run  orchestrator init  for interactive setup.\n"
        )

    if not skip_dep_check:
        from src.infra.config import config as app_config
        from src.infra.cli.wizard.steps.deps import print_dep_table

        click.echo("Checking dependencies...")
        report = print_dep_table(app_config.redis_url)
        if not report.can_start:
            die(
                "Required dependencies are not available. "
                "Run  orchestrator init  to diagnose."
            )
        click.echo()

    from src.infra.factory import build_agent_registry

    registry     = build_agent_registry()
    active_agents = [a for a in registry.list_agents() if a.active]

    if not active_agents:
        die("No active agents found in registry. Register agents first.")

    env   = {**os.environ, "AGENT_MODE": "real"}
    procs = []

    try:
        p = subprocess.Popen(
            ["python", "-m", "src.infra.cli.main", "system", "task-manager"], env=env
        )
        procs.append(("task-manager", p))
        ok("TaskManager started")

        for agent in active_agents:
            worker_env = {**env, "AGENT_ID": agent.agent_id}
            p = subprocess.Popen(
                ["python", "-m", "src.infra.cli.main", "system", "worker"], env=worker_env
            )
            procs.append((agent.agent_id, p))
            ok(f"Worker started: {agent.agent_id}")

        click.echo(f"\nWaiting for workers to heartbeat (timeout={heartbeat_timeout}s)...")
        _wait_for_heartbeats(registry, active_agents, timeout=heartbeat_timeout)

        p = subprocess.Popen(
            [
                "python", "-m", "src.infra.cli.main", "system", "reconciler",
                f"--interval={reconciler_interval}",
                f"--stuck-age={reconciler_stuck_age}",
            ],
            env=env,
        )
        procs.append(("reconciler", p))
        ok("Reconciler started\n")
        click.echo("System ready. Ctrl+C to stop all.\n")

        while True:
            for name, p in procs:
                if p.poll() is not None:
                    warn(f"Process '{name}' exited with code {p.returncode}")
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


@system_group.command("task-manager")
def run_task_manager():
    """Subscribe to task events and coordinate task lifecycle."""
    from src.infra.factory import build_task_manager_handler, build_event_port

    handler = build_task_manager_handler()
    events  = build_event_port()

    click.echo(
        "Task Manager started — listening for "
        "task.created / task.requeued / task.completed / task.failed"
    )

    try:
        for event in events.subscribe_many(
            ["task.created", "task.requeued", "task.completed", "task.failed"],
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
            elif event.type == "task.failed":
                handler.handle_task_failed(task_id)
    except KeyboardInterrupt:
        click.echo("\nTask Manager stopped.")
    except Exception as exc:
        log.exception("task_manager.fatal_error", error=str(exc))
        die(f"Task Manager crashed: {exc}")


@system_group.command("worker")
def run_worker():
    """
    Subscribe to task.assigned events and execute tasks for this agent.

    The worker identity is read from the AGENT_ID environment variable
    (default: agent-worker-001).
    """
    from src.infra.factory import build_event_port, build_worker_handler, build_agent_registry

    from src.infra.config import config as app_config
    agent_id = os.getenv("AGENT_ID") or app_config.agent_id
    events   = build_event_port()
    registry = build_agent_registry()
    handler  = build_worker_handler()

    click.echo(f"Worker {agent_id} started — listening for task.assigned")

    registry.heartbeat(agent_id)
    _start_heartbeat_thread(registry, agent_id)

    try:
        for event in events.subscribe("task.assigned", group="workers", consumer=agent_id):
            assigned_to = event.payload.get("agent_id")
            task_id     = event.payload.get("task_id")
            project_id  = event.payload.get("project_id", "")

            if assigned_to != agent_id:
                log.info("worker.skip_not_mine", task_id=task_id, assigned_to=assigned_to)
                continue

            handler.process(task_id=task_id, project_id=project_id)
    except KeyboardInterrupt:
        click.echo(f"\nWorker {agent_id} stopped.")
    except Exception as exc:
        log.exception("worker.fatal_error", agent_id=agent_id, error=str(exc))
        die(f"Worker {agent_id} crashed: {exc}")


@system_group.command("reconciler")
@click.option("--interval",   default=60,  help="Seconds between reconcile passes")
@click.option("--stuck-age",  default=120, help="Seconds before republishing stuck tasks")
def run_reconciler(interval: int, stuck_age: int):
    """Run the reconciler loop (detects expired leases, dead agents, stuck tasks)."""
    from src.infra.factory import build_reconciler

    reconciler = build_reconciler(
        interval_seconds=interval,
        stuck_task_min_age_seconds=stuck_age,
    )
    click.echo(f"Reconciler started — interval={interval}s, stuck-age={stuck_age}s")

    try:
        reconciler.run_forever()
    except KeyboardInterrupt:
        click.echo("\nReconciler stopped.")
    except Exception as exc:
        log.exception("reconciler.fatal_error", error=str(exc))
        die(f"Reconciler crashed: {exc}")


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _wait_for_heartbeats(registry, agents, timeout: int = 30) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        fresh = [registry.get(a.agent_id) for a in agents]
        if all(a and a.is_alive() for a in fresh):
            ok("All workers alive\n")
            return
        time.sleep(1)
    warn("Some workers did not heartbeat in time — proceeding anyway")


def _start_heartbeat_thread(registry, agent_id: str) -> None:
    import threading

    def _beat():
        while True:
            time.sleep(_HEARTBEAT_INTERVAL)
            try:
                registry.heartbeat(agent_id)
            except Exception as exc:
                log.warning("heartbeat.failed", agent_id=agent_id, error=str(exc))

    threading.Thread(target=_beat, daemon=True, name=f"heartbeat-{agent_id}").start()
