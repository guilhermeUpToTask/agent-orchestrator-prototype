"""
src/infra/cli/system/commands.py — System daemon commands.

Commands:
  orchestrator system start          — boot all daemons (api + task-manager + workers + reconciler)
  orchestrator system api            — run the FastAPI server
  orchestrator system task-manager   — run the task manager event loop
  orchestrator system worker         — run a worker event loop
  orchestrator system reconciler     — run the reconciler loop
"""

from __future__ import annotations

import os
import signal
import subprocess
import time

import click
import structlog

from src.infra.cli.error_handler import catch_domain_errors, die, warn, ok

log = structlog.get_logger(__name__)

# AGENT_ID and HEARTBEAT_INTERVAL_SECONDS are resolved lazily from OrchestratorConfig
# inside each command that needs them — no module-level env reads.
_HEARTBEAT_INTERVAL = 30  # seconds between registry heartbeat pings


@click.group("system")
def system_group():
    """System daemon management."""


@system_group.command("start")
@catch_domain_errors
@click.option("--reconciler-interval", default=60, help="Reconciler poll interval (seconds)")
@click.option("--reconciler-stuck-age", default=120, help="Stuck-task threshold (seconds)")
@click.option("--heartbeat-timeout", default=30, help="Worker heartbeat wait (seconds)")
@click.option("--api-port", default=8000, help="Port for the FastAPI server")
@click.option("--no-api", is_flag=True, default=False, help="Do not boot the API server")
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
    api_port: int,
    no_api: bool,
    skip_dep_check: bool,
):
    """
    Boot the full system: api + task-manager + workers + reconciler.

    Active workers are read from the agent registry (active: true).
    Boot order is enforced: dependencies are verified before any child
    process starts, and workers must heartbeat before the reconciler runs.
    SIGINT/SIGTERM on this process gracefully stops every child daemon.
    """
    from src.infra.settings import GlobalConfigStore

    store = GlobalConfigStore()
    if not store.exists():
        store.generate_defaults()
        click.echo(
            "  ℹ  No .orchestrator/config.json found — generated with defaults.\n"
            "     Run  orchestrator init  for interactive setup.\n"
        )

    from src.infra.container import AppContainer

    app = AppContainer.from_env()

    # Verify dependencies BEFORE booting any sub-process.
    if not skip_dep_check:
        from src.infra.cli.wizard.steps.deps import print_dep_table

        click.echo("Checking dependencies...")
        report = print_dep_table(app.ctx.machine.redis_url)
        if not report.can_start:
            die("Required dependencies are not available. Run  orchestrator init  to diagnose.")
        click.echo()

    registry = app.agent_registry
    active_agents = [a for a in registry.list_agents() if a.active]

    if not active_agents:
        die("No active agents found in registry. Register agents first.")

    # Children inherit the resolved mode (env > config.json > default) so
    # `AGENT_MODE=dry-run orchestrator system start` works as documented.
    env = {**os.environ, "AGENT_MODE": app.ctx.machine.mode}
    procs: list[tuple[str, "subprocess.Popen"]] = []

    _install_sigterm_handler()

    try:
        if not no_api:
            _spawn(procs, "api", ["system", "api", f"--port={api_port}"], env)
            ok(f"API server started on port {api_port}")

        _spawn(procs, "task-manager", ["system", "task-manager"], env)
        ok("TaskManager started")

        for agent in active_agents:
            _spawn(
                procs,
                agent.agent_id,
                ["system", "worker", "--agent-id", agent.agent_id],
                env,
            )
            ok(f"Worker started: {agent.agent_id}")

        click.echo(f"\nWaiting for workers to heartbeat (timeout={heartbeat_timeout}s)...")
        _wait_for_heartbeats(registry, active_agents, timeout=heartbeat_timeout)

        _spawn(
            procs,
            "reconciler",
            [
                "system",
                "reconciler",
                f"--interval={reconciler_interval}",
                f"--stuck-age={reconciler_stuck_age}",
            ],
            env,
        )
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
        _shutdown_processes(procs)
        click.echo("All processes stopped.")


@system_group.command("task-manager")
@catch_domain_errors
def run_task_manager():
    """Subscribe to task events and coordinate task lifecycle."""
    from src.infra.container import AppContainer

    app = AppContainer.from_env()
    handler = app.task_manager_handler
    events = app.event_port

    click.echo(
        "Task Manager started — listening for "
        "task.created / task.requeued / task.completed / task.failed"
    )

    handlers = {
        "task.created": handler.handle_task_created,
        "task.requeued": handler.handle_task_requeued,
        "task.completed": handler.handle_task_completed,
        "task.failed": handler.handle_task_failed,
    }

    try:
        for event in events.subscribe_many(
            ["task.created", "task.requeued", "task.completed", "task.failed"],
            group="task-manager",
            consumer="tm-1",
        ):
            task_id = event.payload.get("task_id")
            if not task_id:
                continue
            fn = handlers.get(event.type)
            if fn is None:
                continue
            fn(task_id)
            events.ack(event, group="task-manager")
    except KeyboardInterrupt:
        click.echo("\nTask Manager stopped.")
    except Exception as exc:
        log.exception("task_manager.fatal_error", error=str(exc))
        die(f"Task Manager crashed: {exc}")


@system_group.command("worker")
@catch_domain_errors
@click.option("--agent-id", required=True, envvar="AGENT_ID", help="Worker identity (required)")
def run_worker(agent_id: str):
    """
    Subscribe to task.assigned events and execute tasks for this agent.
    """
    from src.infra.container import AppContainer

    app = AppContainer.from_env()
    registry = app.agent_registry
    events = app.event_port

    # Validate the provided agent_id actually exists and is active
    agent = registry.get(agent_id)
    if not agent:
        die(f"Agent '{agent_id}' not found in registry. Run `orchestrator agents create` first.")
        return
    if not agent.active:
        die(f"Agent '{agent_id}' is marked as inactive in the registry.")
        return

    handler = app.get_worker_handler(agent_id)

    click.echo(f"Worker {agent_id} started — listening for task.assigned")

    registry.heartbeat(agent_id)
    _start_heartbeat_thread(registry, agent_id)

    try:
        for event in events.subscribe("task.assigned", group="workers", consumer=agent_id):
            assigned_to = event.payload.get("agent_id")
            task_id = event.payload.get("task_id")
            project_id = event.payload.get("project_id", "")

            if assigned_to != agent_id:
                log.info("worker.skip_not_mine", task_id=task_id, assigned_to=assigned_to)
                events.ack(event, group="workers")
                continue

            handler.process(task_id=task_id, project_id=project_id)
            events.ack(event, group="workers")
    except KeyboardInterrupt:
        click.echo(f"\nWorker {agent_id} stopped.")
    except Exception as exc:
        log.exception("worker.fatal_error", agent_id=agent_id, error=str(exc))
        die(f"Worker {agent_id} crashed: {exc}")


@system_group.command("reconciler")
@click.option("--interval", default=60, help="Seconds between reconcile passes")
@click.option("--stuck-age", default=120, help="Seconds before republishing stuck tasks")
@catch_domain_errors
def run_reconciler(interval: int, stuck_age: int):
    """Run the reconciler loop (detects expired leases, dead agents, stuck tasks)."""
    from src.infra.container import AppContainer

    app = AppContainer.from_env()
    reconciler = app.get_reconciler(
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


def _spawn(
    procs: list[tuple[str, subprocess.Popen]],
    name: str,
    cli_args: list[str],
    env: dict[str, str],
) -> subprocess.Popen:
    """Boot a child daemon through the CLI entry point and track it."""
    p = subprocess.Popen(["python", "-m", "src.infra.cli.main", *cli_args], env=env)
    procs.append((name, p))
    log.info("system.daemon_started", daemon=name, pid=p.pid)
    return p


def _install_sigterm_handler() -> None:
    """Route SIGTERM through the KeyboardInterrupt path so the finally
    block tears children down the same way Ctrl+C does."""

    def _handle(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _handle)


def _shutdown_processes(
    procs: list[tuple[str, subprocess.Popen]], timeout: float = 5.0
) -> None:
    """Terminate all children, escalating to SIGKILL after *timeout* seconds."""
    for _name, p in procs:
        if p.poll() is None:
            p.terminate()

    deadline = time.time() + timeout
    for name, p in procs:
        try:
            p.wait(timeout=max(0.1, deadline - time.time()))
        except subprocess.TimeoutExpired:
            warn(f"Process '{name}' did not stop in time — killing")
            p.kill()
            p.wait()
        log.info("system.daemon_stopped", daemon=name, returncode=p.returncode)


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


@system_group.command("api")
@click.option("--port", default=8000, help="Port to run the API server on")
@click.option(
    "--reload",
    is_flag=True,
    default=False,
    help="Enable auto-reload (development only; spawns a watcher process)",
)
def run_api(port: int, reload: bool):
    """Run the FastAPI server for the AIPOM frontend."""
    import uvicorn

    click.echo(f"Starting API server on port {port}...")
    uvicorn.run(
        "src.api.server:create_app",
        host="0.0.0.0",
        port=port,
        factory=True,
        reload=reload,
    )
