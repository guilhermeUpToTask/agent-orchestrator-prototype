"""
src/cli.py — Command-line entry point.

Usage:
  python -m src.cli init              # Run setup wizard → .orchestrator/config.json
  python -m src.cli start             # Boot full system from registry.json
  python -m src.cli task-manager      # Start task manager event loop
  python -m src.cli worker            # Start worker event loop
  python -m src.cli reconciler        # Start reconciler loop
  python -m src.cli create-task       # Create a task (ID auto-generated)
  python -m src.cli list-tasks        # Print task statuses
  python -m src.cli register-agent    # Register an agent in the registry
  python -m src.cli task retry <id>   # Manually requeue a failed/stuck task
  python -m src.cli task delete <id>  # Remove a single task record
  python -m src.cli task prune        # Delete ALL task records
  python -m src.cli project reset     # Full reset: tasks + branches + agents
"""

from __future__ import annotations

import itertools
import os
import sys
import time
from pathlib import Path

import click
import structlog
from src.wizard import run_wizard

structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
log = structlog.get_logger()

_AGENT_ID = os.getenv("AGENT_ID", "agent-worker-001")
_HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "30"))


@click.group()
def cli():
    """Agent Orchestrator CLI."""
    pass


# ---------------------------------------------------------------------------
# Init / wizard
# ---------------------------------------------------------------------------


@cli.command("init")
@click.option(
    "--defaults",
    is_flag=True,
    default=False,
    help="Skip interactive prompts and write a config.json with default values.",
)
def init_wizard(defaults: bool):
    """
    Run the setup wizard to create .orchestrator/config.json.

    Collects project_name, source_repo_url, and redis_url, verifies that
    Redis, git, and at least one agent runtime are available, then writes
    .orchestrator/config.json.  If the agent registry is empty you will be
    offered a chance to register a first agent interactively.

    Skip prompts with --defaults to write a config with safe defaults
    (useful in CI or scripted environments).
    """
    from src.orchestrator_config_manager import OrchestratorConfigManager  # noqa: PLC0415

    if defaults:
        manager = OrchestratorConfigManager()
        data = manager.generate_defaults()
        click.echo(f"✓ Default config written → {manager.config_path}")
        for k, v in data.items():
            click.echo(f"  {k}: {v}")
        return

    success = run_wizard()
    sys.exit(0 if success else 1)


def _ensure_config() -> None:
    """
    Auto-generate .orchestrator/config.json with defaults if it doesn't exist.
    Called at the start of commands that need infra (start, create-task, etc.)
    so a first-time user gets a working config without running `init`.
    """
    from src.orchestrator_config_manager import OrchestratorConfigManager

    manager = OrchestratorConfigManager()
    if not manager.exists():
        manager.generate_defaults()
        click.echo(
            "  ℹ  No .orchestrator/config.json found — generated with defaults.\n"
            "     Run  orchestrator init  for interactive setup.\n"
        )


# ---------------------------------------------------------------------------
# System boot — starts all processes from registry
# ---------------------------------------------------------------------------


@cli.command("start")
@click.option(
    "--reconciler-interval", default=60, help="Reconciler interval in seconds (default: 60)"
)
@click.option(
    "--reconciler-stuck-age",
    default=120,
    help="Seconds before reconciler republishes a stuck CREATED/REQUEUED task (default: 120)",
)
@click.option("--heartbeat-timeout", default=30, help="Seconds to wait for worker heartbeats")
@click.option(
    "--skip-dep-check",
    is_flag=True,
    default=False,
    help="Skip dependency verification (not recommended for production)",
)
def start_system(
    reconciler_interval: int,
    reconciler_stuck_age: int,
    heartbeat_timeout: int,
    skip_dep_check: bool,
):
    """
    Boot the full system: task-manager + all active workers + reconciler.
    Active workers are read from registry.json (active: true).
    Boot order is enforced: workers must heartbeat before reconciler starts.
    """
    import subprocess

    _ensure_config()

    # Dependency check before we try to boot any subprocesses
    if not skip_dep_check:
        from src.infra.config import config as app_config
        from src.dependency_checker import DependencyChecker

        click.echo("Checking dependencies...")
        checker = DependencyChecker(redis_url=app_config.redis_url)
        report = checker.run()
        for r in report.results:
            icon = "✓" if r.ok else "✗"
            click.echo(f"  {icon}  {r.name}: {r.message}")
        if not report.can_start:
            click.echo(
                "\n✗  Required dependencies are not available. "
                "Run  orchestrator init  to diagnose.\n",
                err=True,
            )
            sys.exit(1)
        click.echo()

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
        p = subprocess.Popen(
            [
                "python",
                "-m",
                "src.cli",
                "reconciler",
                f"--interval={reconciler_interval}",
                f"--stuck-age={reconciler_stuck_age}",
            ],
            env=env,
        )
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
    from src.domain.entities.agent import AgentProps as _A; is_agent_alive = lambda a, t=60: a.is_alive(t)

    deadline = time.time() + timeout
    while time.time() < deadline:
        # Re-read registry on each check so we see fresh heartbeats
        fresh_agents = [registry.get(a.agent_id) for a in agents]
        if all(a and is_agent_alive(a) for a in fresh_agents):
            click.echo("✓ All workers alive\n")
            return
        time.sleep(1)
    click.echo("⚠  Some workers did not heartbeat in time — proceeding anyway", err=True)


# ---------------------------------------------------------------------------
# Task Manager event loop
# ---------------------------------------------------------------------------


@cli.command("task-manager")
def run_task_manager():
    """Subscribe to task events and assign / unblock / recover tasks."""
    from src.infra.factory import build_task_manager_handler, build_event_port

    handler = build_task_manager_handler()
    events = build_event_port()

    click.echo(
        "Task Manager started — listening for task.created / task.requeued / task.completed / task.failed"
    )

    # subscribe_many reads all streams in a single XREADGROUP call.
    # Using itertools.chain() on blocking generators would silently starve
    # task.requeued / task.completed / task.failed — the first stream blocks forever.
    #
    # task.failed is emitted by both the worker (agent failure) and the
    # reconciler (dead agent / expired lease).  The task manager is the
    # single place that decides whether to requeue or cancel.
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
            except Exception as e:
                import structlog

                structlog.get_logger(__name__).warning("heartbeat.failed", error=str(e))

    t = threading.Thread(target=_beat, daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# Reconciler
# ---------------------------------------------------------------------------


@cli.command("reconciler")
@click.option("--interval", default=60, help="Seconds between reconcile passes (default: 60)")
@click.option(
    "--stuck-age",
    default=120,
    help="Seconds a CREATED/REQUEUED task must sit unassigned before reconciler republishes it (default: 120)",
)
def run_reconciler(interval: int, stuck_age: int):
    """Run the reconciler loop (detects expired leases, dead agents, stuck tasks)."""
    from src.infra.factory import build_reconciler

    reconciler = build_reconciler(
        interval_seconds=interval,
        stuck_task_min_age_seconds=stuck_age,
    )
    click.echo(f"Reconciler started — interval={interval}s, stuck-age={stuck_age}s")
    reconciler.run_forever()


# ---------------------------------------------------------------------------
# Create task
# ---------------------------------------------------------------------------


@cli.command("create-task")
@click.option("--title", required=True, help="Short title for the task")
@click.option("--description", required=True, help="Full description of what the agent must do")
@click.option(
    "--feature-id", default=None, help="Feature/project group ID (auto-generated if omitted)"
)
@click.option("--capability", required=True, help="Required agent capability, e.g. code:backend")
@click.option(
    "--allow", multiple=True, required=True, help="File the agent is allowed to modify (repeatable)"
)
@click.option(
    "--test", default=None, help="Shell command to verify the result, e.g. 'python3 hello.py'"
)
@click.option("--criteria", multiple=True, help="Acceptance criteria lines (repeatable)")
@click.option("--depends-on", multiple=True, help="Task IDs this task depends on (repeatable)")
@click.option("--max-retries", default=2, help="Max retry attempts on failure")
@click.option("--min-version", default=">=1.0.0", help="Minimum agent version constraint")
def create_task(
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
    """
    Create a task and emit task.created.

    The task ID is generated automatically from a UUID — no manual file
    naming required. The YAML is written to workflow/tasks/ as the source
    of truth. If the event is lost (e.g. crash), the reconciler will
    re-emit it on the next pass.

    Example:
      python -m src.cli create-task \\
        --title "Create hello world" \\
        --description "Create hello.py that prints Hello from agent!" \\
        --capability code:backend \\
        --allow hello.py \\
        --test "python3 hello.py | grep -q 'Hello from agent!'"
    """
    from src.infra.factory import build_task_creation_service

    mode = os.getenv("AGENT_MODE", "dry-run")
    if mode != "real":
        click.echo(
            "⚠  AGENT_MODE is not 'real' (current: '{}').\n"
            "   Events will NOT reach Redis. "
            "Run with: AGENT_MODE=real python -m src.cli create-task ...".format(mode),
            err=True,
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

    task_id = task.task_id
    fid = task.feature_id

    click.echo(f"✓ Task created: {task_id}")
    click.echo(f"  title:      {title}")
    click.echo(f"  feature:    {fid}")
    click.echo(f"  capability: {capability}")
    click.echo(f"  files:      {', '.join(allow)}")
    if test:
        click.echo(f"  test:       {test}")
    if depends_on:
        click.echo(f"  depends on: {', '.join(depends_on)}")


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
@click.option("--runtime-type", default="gemini", help="gemini | claude | pi | dry-run")
@click.option(
    "--runtime-config", default="{}", help='JSON string, e.g. \'{"model":"gemini-2.0-pro"}\''
)
def register_agent(
    agent_id: str,
    name: str,
    capabilities: str,
    version: str,
    active: bool,
    runtime_type: str,
    runtime_config: str,
):
    """Register a new agent in the registry."""
    import json
    from src.domain import AgentProps
    from src.infra.factory import build_agent_registry

    try:
        config = json.loads(runtime_config)
    except json.JSONDecodeError as e:
        click.echo(f"✗ Invalid --runtime-config JSON: {e}", err=True)
        sys.exit(1)

    agent = AgentProps(
        agent_id=agent_id,
        name=name,
        capabilities=[c.strip() for c in capabilities.split(",")],
        version=version,
        active=active,
        runtime_type=runtime_type,
        runtime_config=config,
    )
    registry = build_agent_registry()
    registry.register(agent)
    status = "active" if active else "inactive"
    click.echo(f"✓ Agent registered: {agent_id} ({status}, runtime: {runtime_type})")


# ---------------------------------------------------------------------------
# task subcommand group
# ---------------------------------------------------------------------------


@cli.group("task")
def task_group():
    """Manage individual tasks."""
    pass


@task_group.command("retry")
@click.argument("task_id")
def task_retry(task_id: str):
    """
    Manually requeue a task so the system attempts it again.

    Works on tasks in any non-MERGED status.  The task's retry counter is
    NOT incremented — this is an operator override, not an automatic retry.

    Example:
      orchestrator task retry task-abc123
    """
    from src.infra.factory import build_task_retry_usecase

    usecase = build_task_retry_usecase()
    try:
        result = usecase.execute(task_id)
    except KeyError:
        click.echo(f"✗  Task not found: {task_id}", err=True)
        sys.exit(1)
    except ValueError as exc:
        click.echo(f"✗  {exc}", err=True)
        sys.exit(1)

    click.echo(f"✓  Task {task_id} requeued  ({result.previous_status.value} → requeued)")


@task_group.command("delete")
@click.argument("task_id")
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip confirmation prompt")
def task_delete(task_id: str, yes: bool):
    """
    Permanently delete a single task record from the filesystem.

    The task YAML file is removed.  Any Git branch or Redis lease associated
    with the task is NOT cleaned up automatically — use  project reset  for a
    full teardown.

    Example:
      orchestrator task delete task-abc123
      orchestrator task delete task-abc123 --yes
    """
    from src.infra.factory import build_task_repo

    repo = build_task_repo()
    task = repo.get(task_id)
    if task is None:
        click.echo(f"✗  Task not found: {task_id}", err=True)
        sys.exit(1)

    if not yes:
        click.confirm(
            f"  Delete task {task_id} (status: {task.status.value})?",
            abort=True,
        )

    repo.delete(task_id)
    click.echo(f"✓  Task {task_id} deleted")


@task_group.command("prune")
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip confirmation prompt")
@click.option(
    "--status",
    multiple=True,
    default=[],
    help="Only prune tasks with this status (repeatable). Omit to prune ALL tasks.",
)
def task_prune(yes: bool, status: tuple):
    """
    Delete ALL task records (or tasks matching a given status).

    Examples:
      orchestrator task prune                         # delete everything
      orchestrator task prune --status failed         # only failed tasks
      orchestrator task prune --status failed --status canceled
      orchestrator task prune --yes                   # skip confirmation
    """
    from src.infra.factory import build_task_repo
    from src.domain import TaskStatus

    repo = build_task_repo()
    all_tasks = repo.list_all()

    if status:
        try:
            filter_statuses = {TaskStatus(s) for s in status}
        except ValueError as exc:
            click.echo(f"✗  Invalid status value: {exc}", err=True)
            sys.exit(1)
        targets = [t for t in all_tasks if t.status in filter_statuses]
        label = f"with status {'/'.join(status)}"
    else:
        targets = list(all_tasks)
        label = "ALL"

    if not targets:
        click.echo(f"  No tasks found ({label}).")
        return

    if not yes:
        click.confirm(
            f"  Delete {len(targets)} task(s) ({label})?",
            abort=True,
        )

    for task in targets:
        repo.delete(task.task_id)

    click.echo(f"✓  Deleted {len(targets)} task(s) ({label})")


# ---------------------------------------------------------------------------
# project subcommand group
# ---------------------------------------------------------------------------


@cli.group("project")
def project_group():
    """Manage the active project."""
    pass


@project_group.command("reset")
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip confirmation prompt")
@click.option(
    "--keep-agents",
    is_flag=True,
    default=False,
    help="Keep the agent registry intact (only delete tasks and branches)",
)
def project_reset(yes: bool, keep_agents: bool):
    """
    Full project reset: delete all tasks, agent leases, and Git branches.

    What gets deleted:
      • All task YAML files in the tasks directory
      • All Redis leases held by agents
      • All remote Git branches that match the task-branch naming convention
      • The agent registry  (unless --keep-agents is given)

    This is a destructive, irreversible operation intended for development
    or when you want to start a project from a clean slate.

    Example:
      orchestrator project reset
      orchestrator project reset --keep-agents
      orchestrator project reset --yes
    """
    import shutil

    from src.infra.factory import build_task_repo, build_agent_registry, build_lease_port
    from src.infra.config import config as app_config

    if not yes:
        extra = "" if keep_agents else " + agents"
        click.confirm(
            f"  Reset project '{app_config.project_name}' (tasks{extra} + git branches)?",
            abort=True,
        )

    errors: list[str] = []

    # 1. Delete all tasks ─────────────────────────────────────────────────────
    try:
        repo = build_task_repo()
        tasks = repo.list_all()
        task_ids = [t.task_id for t in tasks]
        for tid in task_ids:
            repo.delete(tid)
        click.echo(f"  ✓  Deleted {len(task_ids)} task(s)")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"tasks: {exc}")
        click.echo(f"  ⚠  Could not delete tasks: {exc}", err=True)

    # 2. Release Redis leases ─────────────────────────────────────────────────
    try:
        lease_port = build_lease_port()
        # Best-effort: revoke leases for every task we just removed.
        # get_lease_agent lets us find the token; if not found we skip.
        released = 0
        for tid in task_ids:
            try:
                if lease_port.is_lease_active(tid):
                    # We don't have the token here; revoke_lease accepts the
                    # task-scoped key on the in-memory adapter, but on Redis
                    # we need the token.  Use the agent_id as the key lookup.
                    agent = lease_port.get_lease_agent(tid)
                    if agent:
                        lease_port.revoke_lease(f"{tid}:{agent}")
                        released += 1
            except Exception:  # noqa: BLE001
                pass
        if released:
            click.echo(f"  ✓  Released {released} lease(s)")
    except Exception as exc:  # noqa: BLE001
        click.echo(f"  ⚠  Could not release leases: {exc}", err=True)

    # 3. Delete Git branches ──────────────────────────────────────────────────
    _delete_task_branches(task_ids, app_config, errors)

    # 4. Clear agent registry (unless --keep-agents) ──────────────────────────
    if not keep_agents:
        try:
            registry = build_agent_registry()
            agents = registry.list_agents()
            for agent in agents:
                registry.deregister(agent.agent_id)
            click.echo(f"  ✓  Removed {len(agents)} agent(s) from registry")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"registry: {exc}")
            click.echo(f"  ⚠  Could not clear registry: {exc}", err=True)

    if errors:
        click.echo(f"\n⚠  Reset completed with {len(errors)} error(s). See above.", err=True)
    else:
        click.echo(f"\n✓  Project '{app_config.project_name}' reset complete")


def _delete_task_branches(
    task_ids: list[str],
    app_config: object,
    errors: list[str],
) -> None:
    """Delete remote Git branches whose names start with task-<task_id>."""
    import subprocess

    repo_url = getattr(app_config, "repo_url", None)
    if not repo_url:
        return

    deleted = 0
    for tid in task_ids:
        branch = f"task-{tid}"
        try:
            result = subprocess.run(
                ["git", "push", repo_url, f":{branch}"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0:
                deleted += 1
        except Exception:  # noqa: BLE001
            pass

    if deleted:
        click.echo(f"  ✓  Deleted {deleted} git branch(es)")


if __name__ == "__main__":
    cli()
