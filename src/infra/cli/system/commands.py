"""
src/infra/cli/system/commands.py — System daemon commands.

Commands:
  orchestrate system start          — boot the system (api with embedded coordinators + workers)
  orchestrate system api            — run the FastAPI server (hosts task-manager,
                                      goal orchestrator and reconciler as lifespan threads)
  orchestrate system task-manager   — run the task manager loop standalone
  orchestrate system worker         — run a worker event loop
  orchestrate system reconciler     — run the reconciler loop standalone

The standalone task-manager/reconciler commands exist as escape hatches for
running coordinators outside the API process; set
ORCHESTRATOR_EMBED_COORDINATORS=0 on the API when you use them, or task/goal
state gains a second writer process.
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
    Boot the full system: the API (which hosts the task-manager, goal
    orchestrator and reconciler on lifespan threads) plus one worker
    process per active agent.

    Active workers are read from the agent registry (active: true).
    Crashed workers are restarted with exponential backoff; an API exit
    shuts the whole system down. SIGINT/SIGTERM on this process gracefully
    stops every child daemon.
    """
    from src.infra.settings import GlobalConfigStore

    store = GlobalConfigStore()
    if not store.exists():
        store.generate_defaults()
        click.echo(
            "  ℹ  No .orchestrator/config.json found — generated with defaults.\n"
            "     Run  orchestrate init  for interactive setup.\n"
        )

    from src.infra.container import AppContainer

    app = AppContainer.from_env()

    # Verify dependencies BEFORE booting any sub-process.
    if not skip_dep_check:
        from src.infra.cli.wizard.steps.deps import print_dep_table

        click.echo("Checking dependencies...")
        report = print_dep_table(app.ctx.machine.redis_url)
        if not report.can_start:
            die("Required dependencies are not available. Run  orchestrate init  to diagnose.")
        click.echo()

    _destroy_legacy_workers_group(app)

    registry = app.agent_registry
    active_agents = [a for a in registry.list_agents() if a.active]

    if not active_agents:
        die("No active agents found in registry. Register agents first.")

    # Children inherit the resolved mode (env > config.json > default) so
    # `AGENT_MODE=dry-run orchestrate system start` works as documented.
    # Reconciler tuning flows to the API process, which hosts it.
    env = {
        **os.environ,
        "AGENT_MODE": app.ctx.machine.mode,
        "RECONCILER_INTERVAL": str(reconciler_interval),
        "RECONCILER_STUCK_AGE": str(reconciler_stuck_age),
    }
    procs: list[list] = []  # mutable [name, Popen] entries (supervisor swaps Popen)

    _install_sigterm_handler()

    try:
        if not no_api:
            _spawn(procs, "api", ["system", "api", f"--port={api_port}"], env)
            ok(f"API server started on port {api_port} (coordinators embedded)")
        else:
            warn(
                "--no-api: task-manager / goal orchestrator / reconciler are "
                "hosted by the API process and will NOT run."
            )

        worker_args: dict[str, list[str]] = {}
        for agent in active_agents:
            args = ["system", "worker", "--agent-id", agent.agent_id]
            worker_args[agent.agent_id] = args
            _spawn(procs, agent.agent_id, args, env)
            ok(f"Worker started: {agent.agent_id}")

        click.echo(f"\nWaiting for workers to heartbeat (timeout={heartbeat_timeout}s)...")
        _wait_for_heartbeats(registry, active_agents, timeout=heartbeat_timeout)

        click.echo("System ready. Ctrl+C to stop all.\n")
        _supervise(procs, worker_args, env)

    except KeyboardInterrupt:
        click.echo("\nShutting down...")
    finally:
        _shutdown_processes(procs)
        click.echo("All processes stopped.")


@system_group.command("task-manager")
@catch_domain_errors
def run_task_manager():
    """Subscribe to task events and coordinate task lifecycle (standalone)."""
    from src.app.runners import run_task_manager_loop
    from src.infra.container import AppContainer

    app = AppContainer.from_env()

    click.echo(
        "Task Manager started — listening for "
        "task.created / task.requeued / task.completed / task.failed"
    )

    try:
        run_task_manager_loop(
            handler=app.task_manager_handler,
            events=app.event_port,
            stop=lambda: False,
        )
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
        die(f"Agent '{agent_id}' not found in registry. Run `orchestrate agents create` first.")
        return
    if not agent.active:
        die(f"Agent '{agent_id}' is marked as inactive in the registry.")
        return

    handler = app.get_worker_handler(agent_id)

    click.echo(f"Worker {agent_id} started — listening for task.assigned")

    registry.heartbeat(agent_id)
    _start_heartbeat_thread(registry, agent_id)

    # Each worker uses its own consumer group so every group receives every
    # task.assigned event. A shared group would deliver each event to exactly
    # one worker — usually the wrong one, which would ack and drop it.
    group = f"worker-{agent_id}"

    try:
        # Outer loop: the Redis generator blocks forever, but the in-memory
        # (dry-run) one returns once drained — poll so the worker stays up.
        while True:
            for event in events.subscribe("task.assigned", group=group, consumer=agent_id):
                assigned_to = event.payload.get("agent_id")
                task_id = event.payload.get("task_id")
                project_id = event.payload.get("project_id", "")

                if assigned_to != agent_id:
                    log.debug("worker.skip_not_mine", task_id=task_id, assigned_to=assigned_to)
                    events.ack(event, group=group)
                    continue

                handler.process(task_id=task_id, project_id=project_id)
                events.ack(event, group=group)
            time.sleep(0.5)
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
    procs: list[list],
    name: str,
    cli_args: list[str],
    env: dict[str, str],
) -> subprocess.Popen:
    """Boot a child daemon through the CLI entry point and track it."""
    p = subprocess.Popen(["python", "-m", "src.infra.cli.main", *cli_args], env=env)
    procs.append([name, p])
    log.info("system.daemon_started", daemon=name, pid=p.pid)
    return p


_BACKOFF_BASE_SECONDS = 2.0
_BACKOFF_CAP_SECONDS = 60.0
_MAX_CONSECUTIVE_CRASHES = 5
_HEALTHY_RESET_SECONDS = 600.0


def _supervise(procs: list[list], worker_args: dict[str, list[str]], env: dict) -> None:
    """Supervise children: restart crashed workers with exponential backoff.

    Each crash is warned exactly once. A worker that stays up for
    _HEALTHY_RESET_SECONDS gets its crash counter reset; one that crashes
    _MAX_CONSECUTIVE_CRASHES times in a row is abandoned. An API exit ends
    supervision (and with it, the whole system).
    """
    state: dict[str, dict] = {}

    while True:
        now = time.time()
        for entry in procs:
            name, p = entry
            st = state.setdefault(
                name, {"restarts": 0, "started_at": now, "restart_at": None, "gave_up": False}
            )

            if p.poll() is None:
                if st["restarts"] and now - st["started_at"] > _HEALTHY_RESET_SECONDS:
                    st["restarts"] = 0
                continue

            if name not in worker_args:
                warn(f"Process '{name}' exited with code {p.returncode} — shutting down")
                return

            if st["gave_up"]:
                continue

            if st["restart_at"] is None:
                if st["restarts"] >= _MAX_CONSECUTIVE_CRASHES:
                    warn(
                        f"Worker '{name}' crashed {st['restarts']} times in a row — "
                        "giving up on it"
                    )
                    st["gave_up"] = True
                    continue
                delay = min(
                    _BACKOFF_CAP_SECONDS, _BACKOFF_BASE_SECONDS * (2 ** st["restarts"])
                )
                warn(
                    f"Worker '{name}' exited with code {p.returncode} — "
                    f"restarting in {delay:.0f}s"
                )
                st["restart_at"] = now + delay
            elif now >= st["restart_at"]:
                new_proc = subprocess.Popen(
                    ["python", "-m", "src.infra.cli.main", *worker_args[name]], env=env
                )
                entry[1] = new_proc
                st["restarts"] += 1
                st["started_at"] = now
                st["restart_at"] = None
                log.info(
                    "system.worker_restarted",
                    daemon=name,
                    pid=new_proc.pid,
                    attempt=st["restarts"],
                )

        time.sleep(1)


def _destroy_legacy_workers_group(app) -> None:
    """Remove the pre-per-agent-group shared "workers" consumer group.

    Older versions had all workers share one group on events:task.assigned,
    which left stale pending entries behind. Safe to call repeatedly; no-op
    once the group is gone or in dry-run mode (no Redis).
    """
    if app.ctx.machine.mode == "dry-run":
        return
    try:
        if app._redis.xgroup_destroy("events:task.assigned", "workers"):
            log.info("system.legacy_workers_group_destroyed")
    except Exception:
        pass  # stream doesn't exist yet — nothing to migrate


def _install_sigterm_handler() -> None:
    """Route SIGTERM through the KeyboardInterrupt path so the finally
    block tears children down the same way Ctrl+C does."""

    def _handle(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _handle)


def _shutdown_processes(procs: list[list], timeout: float = 5.0) -> None:
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
