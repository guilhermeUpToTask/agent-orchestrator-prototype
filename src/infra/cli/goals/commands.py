"""
src/infra/cli/goals/commands.py — Goal management commands.

Commands:
  orchestrator goals init      — parse a goal file, create tasks and goal branch
  orchestrator goals run       — start the TaskGraphOrchestrator event loop
  orchestrator goals status    — show goal and task states
  orchestrator goals finalize  — merge completed goal branch into main
"""
from __future__ import annotations

import sys

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
    import os
    from src.infra.goal_file import load_goal_file
    from src.infra.factory import build_goal_init_usecase

    from src.infra.config import config as app_config
    mode = app_config.mode
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
    import os
    from src.infra.factory import build_task_graph_orchestrator

    from src.infra.config import config as app_config
    mode = app_config.mode
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
    from src.domain.aggregates.goal import GoalStatus

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
    import os
    from src.infra.factory import build_goal_repo, build_goal_finalize_usecase

    from src.infra.config import config as app_config
    mode = app_config.mode
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
    sha = usecase.execute(goal_id)
    ok(f"Merged '{goal.branch}' → main  (commit: {sha[:12]})")
