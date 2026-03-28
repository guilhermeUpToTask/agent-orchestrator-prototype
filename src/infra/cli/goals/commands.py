"""
src/infra/cli/goals/commands.py — Goal management commands.

Commands:
  orchestrator goals init      — parse a goal file, create tasks and goal branch
  orchestrator goals run       — start the TaskGraphOrchestrator event loop
  orchestrator goals status    — show goal and task states
  orchestrator goals finalize  — merge completed goal branch into main
"""
from __future__ import annotations

import click

from src.infra.cli.error_handler import catch_domain_errors, die, info, ok, warn


@click.group("goals")
def goals_group():
    """Manage goals."""


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

@goals_group.command("init")
@click.argument("goal_file", type=click.Path(exists=True, dir_okay=False))
@catch_domain_errors
def goal_init(goal_file: str):
    """
    Parse GOAL_FILE, create all tasks, and set up the goal branch.

    GOAL_FILE must be a YAML file conforming to the GoalSpec schema.
    Emits goal.created and task.created for each task.
    """
    from src.infra.goal_file import load_goal_file
    from src.infra.factory import build_goal_init_usecase

    from src.infra.settings import SettingsService
    mode = SettingsService().load().machine.mode
    if mode != "real":
        warn(
            f"AGENT_MODE is not 'real' (current: '{mode}').\n"
            "   Events will NOT reach Redis. "
            "Run with: AGENT_MODE=real orchestrator goals init <file>"
        )

    spec = load_goal_file(goal_file)
    usecase = build_goal_init_usecase()
    goal = usecase.execute(spec)

    ok(f"Goal initialized: {goal.goal_id}")
    info(f"Name:        {goal.name}")
    info(f"Branch:      {goal.branch}")
    info(f"Tasks:       {len(goal.tasks)}")
    for tid, summary in goal.tasks.items():
        deps = f"  (depends: {', '.join(summary.depends_on)})" if summary.depends_on else ""
        info(f"  • {tid}{deps}")


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

@goals_group.command("run")
@catch_domain_errors
def goal_run():
    """
    Start the TaskGraphOrchestrator event loop.

    Subscribes to the task event stream and drives goal-level coordination
    (branch merges, goal completion detection) until SIGTERM or Ctrl+C.

    Runs indefinitely. Intended to be managed by a process supervisor.
    """
    from src.infra.factory import build_task_graph_orchestrator

    from src.infra.settings import SettingsService
    mode = SettingsService().load().machine.mode
    if mode != "real":
        warn(
            f"AGENT_MODE is not 'real' (current: '{mode}').\n"
            "   The orchestrator needs Redis to receive events."
        )

    orchestrator = build_task_graph_orchestrator()
    info("TaskGraphOrchestrator starting. Press Ctrl+C to stop.")
    orchestrator.run_forever()


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

@goals_group.command("status")
@click.argument("goal_id", required=False)
@catch_domain_errors
def goal_status(goal_id: str | None):
    """
    Show goal progress and per-task status.

    With GOAL_ID: shows detailed status for that goal.
    Without GOAL_ID: lists all goals with a one-line summary each.
    """
    from src.infra.factory import build_goal_repo

    repo = build_goal_repo()

    if goal_id:
        _print_goal_detail(repo.load(goal_id))
    else:
        goals = repo.list_all()
        if not goals:
            info("No goals found.")
            return
        for g in goals:
            merged, total = g.progress()
            status_label = _status_color(g.status.value)
            info(f"{g.goal_id:<28} {status_label:<12} {merged}/{total} tasks  {g.branch}")


def _print_goal_detail(goal) -> None:
    merged, total = goal.progress()
    ok(f"{goal.goal_id}  —  {goal.name}")
    info(f"Status:   {_status_color(goal.status.value)}")
    info(f"Branch:   {goal.branch}")
    info(f"Progress: {merged}/{total} tasks merged")
    if goal.failure_reason:
        warn(f"Failure:  {goal.failure_reason}")
    info("")
    info("Tasks:")
    for tid, summary in goal.tasks.items():
        deps = f" ← {', '.join(summary.depends_on)}" if summary.depends_on else ""
        info(f"  {_task_status_icon(summary.status.value)} {tid:<28} {summary.status.value}{deps}")


def _status_color(status: str) -> str:
    colors = {
        "pending":   "pending",
        "running":   "running",
        "completed": "completed ✓",
        "failed":    "FAILED ✗",
    }
    return colors.get(status, status)


def _task_status_icon(status: str) -> str:
    icons = {
        "created":     "○",
        "assigned":    "→",
        "in_progress": "⋯",
        "succeeded":   "✓",
        "failed":      "✗",
        "canceled":    "⊗",
        "requeued":    "↺",
        "merged":      "●",
    }
    return icons.get(status, "?")


# ---------------------------------------------------------------------------
# finalize
# ---------------------------------------------------------------------------

@goals_group.command("finalize")
@click.argument("goal_id")
@click.option(
    "--yes", "-y", is_flag=True, default=False,
    help="Skip confirmation prompt."
)
@catch_domain_errors
def goal_finalize(goal_id: str, yes: bool):
    """
    Merge a completed goal branch into main.

    GOAL_ID must be in COMPLETED status (all tasks merged into the goal branch).
    This is the only step that writes to main.
    """
    from src.infra.factory import build_goal_repo, build_goal_finalize_usecase

    from src.infra.settings import SettingsService
    mode = SettingsService().load().machine.mode
    if mode != "real":
        warn(
            f"AGENT_MODE is not 'real' (current: '{mode}').\n"
            "   This command will attempt a dry-run git merge."
        )

    repo = build_goal_repo()
    goal = repo.load(goal_id)
    merged, total = goal.progress()

    info(f"Goal:    {goal.goal_id}  ({goal.name})")
    info(f"Branch:  {goal.branch}")
    info(f"Status:  {goal.status.value}")
    info(f"Tasks:   {merged}/{total} merged")

    if not yes:
        click.confirm(
            f"\nMerge '{goal.branch}' into main?",
            abort=True,
        )

    usecase = build_goal_finalize_usecase()
    result = usecase.execute(goal_id)
    pr_info = f"  PR #{result['pr_number']}  {result['pr_url']}" if result.get("pr_number") else ""
    ok(f"Goal '{goal.branch}' finalized  (status: {result['goal_status']}){pr_info}")


# ---------------------------------------------------------------------------
# plan (DEPRECATED)
# ---------------------------------------------------------------------------

@goals_group.command("plan")
@click.argument("user_input")
@click.option(
    "--dispatch", "-d",
    is_flag=True,
    default=False,
    help="Dispatch goals immediately after planning (skips manual confirmation step).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Use stub runtime — shows what would be planned without calling the LLM.",
)
@catch_domain_errors
def goal_plan(user_input: str, dispatch: bool, dry_run: bool):
    """
    DEPRECATED: Use 'orchestrator plan init' instead.

    Run the AI planner to generate a roadmap from USER_INPUT.
    """
    warn("DEPRECATED: `orchestrator goals plan` has been retired.")
    info(f"Received legacy planning input: {user_input!r}")
    info(f"Legacy flags ignored: dispatch={dispatch}, dry_run={dry_run}")
    die(
        "Use the PlannerOrchestrator commands instead:\n"
        "  orchestrator plan init\n"
        "  orchestrator plan architect\n"
        "  orchestrator plan review\n"
        "  orchestrator plan status"
    )


# ---------------------------------------------------------------------------
# dispatch-roadmap (DEPRECATED)
# ---------------------------------------------------------------------------

@goals_group.command("dispatch-roadmap")
@click.argument("session_id")
@click.option(
    "--yes", "-y",
    is_flag=True,
    default=False,
    help="Skip confirmation prompt.",
)
@catch_domain_errors
def goal_dispatch_roadmap(session_id: str, yes: bool):
    """
    DEPRECATED: Use 'orchestrator plan architect' instead.

    Dispatch goals from a previously completed planning session.
    """
    warn("DEPRECATED: `orchestrator goals dispatch-roadmap` has been retired.")
    info(f"Legacy session id ignored: {session_id}")
    info(f"Legacy yes flag ignored: {yes}")
    die("Use `orchestrator plan architect` to approve architecture and dispatch goals.")


# ---------------------------------------------------------------------------
# sessions (DEPRECATED)
# ---------------------------------------------------------------------------

@goals_group.command("sessions")
@click.option("--limit", default=10, help="Number of recent sessions to show.")
@catch_domain_errors
def goal_sessions(limit: int):
    """
    DEPRECATED: Use 'orchestrator plan status' instead.

    List recent planning sessions and their status.
    """
    warn("DEPRECATED: Use 'orchestrator plan status' instead")
    
    from src.infra.factory import build_planner_session_repo

    repo = build_planner_session_repo()
    sessions = repo.list_all()[:limit]

    if not sessions:
        info("No planning sessions found.")
        return

    click.echo()
    for s in sessions:
        goals_info = f"  {len(s.goals_dispatched)} dispatched" if s.goals_dispatched else ""
        errors_info = f"  {len(s.validation_errors)} errors" if s.validation_errors else ""
        status_icon = {"completed": "✓", "failed": "✗", "running": "⋯", "pending": "○"}.get(
            s.status.value, "?"
        )
        click.echo(
            f"  {status_icon} {s.session_id}  [{s.status.value}]{goals_info}{errors_info}"
        )
        click.echo(f"    \"{s.user_input[:70]}\"")
        click.echo(f"    {s.created_at.strftime('%Y-%m-%d %H:%M')} UTC")
        click.echo()
