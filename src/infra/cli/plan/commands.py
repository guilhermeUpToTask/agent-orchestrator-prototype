"""
src/infra/cli/plan/commands.py — CLI commands for strategic planning.

Commands:
  init         — Start discovery phase
  architect    — Run architecture planning
  review       — Run phase review
  status       — Show project plan status
  decision     — Surface mid-phase architectural question
  logs         — Display or tail a planning session log
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
import threading
import time
import uuid

import click

from src.infra.container import AppContainer
from src.infra.container import AppContainer as _AppContainer
from src.infra.cli.error_handler import die, err, info, ok, warn, catch_domain_errors


def _require_project() -> str:
    project = _AppContainer.from_env().ctx.machine.project_name
    if not project:
        die("No project configured.\n  Run: orchestrator init\n  Then try again.")
        return ""
    return project


def _io_handler(question: str) -> str:
    return click.prompt(question, type=str)


def _print_section(title: str) -> None:
    width = 60
    click.echo()
    click.echo("─" * width)
    click.echo(f"  {title}")
    click.echo("─" * width)


@contextlib.contextmanager
def _spinner(message: str):
    frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    stop_event = threading.Event()

    def _spin():
        i = 0
        while not stop_event.is_set():
            click.echo(f"\r{frames[i % len(frames)]}  {message}", nl=False, err=False)
            stop_event.wait(0.1)
            i += 1
        click.echo(f"\r   {message} … done", nl=True)

    if not sys.stdout.isatty():
        click.echo(f"   {message}...")
        yield
        return

    t = threading.Thread(target=_spin, daemon=True)
    t.start()
    try:
        yield
    finally:
        stop_event.set()
        t.join()


def _make_planner_logger(container: AppContainer, mode: str):
    from src.infra.logging.live_logger import LiveLogger
    from src.infra.logging.planner_logger import PlannerLiveLogger

    log_dir = container.paths.logs_dir / "planner"
    log_dir.mkdir(parents=True, exist_ok=True)
    session_id = str(uuid.uuid4())[:8]
    live = LiveLogger(json_log_dir=log_dir)
    return PlannerLiveLogger(live, session_id, mode, log_dir), session_id


def _bind_planner_hooks(orchestrator, planner_log) -> None:
    from src.infra.logging.planner_callback import StreamingPlannerCallback

    callback = StreamingPlannerCallback(planner_log)
    orchestrator.set_turn_callback(callback.on_turn)

    def event_hook(event_type: str, data: dict) -> None:
        if event_type == "decision_proposed":
            planner_log.on_decision_proposed(data.get("id", ""), data.get("domain", ""))
        elif event_type == "phase_proposed":
            planner_log.on_phase_proposed(data.get("name", ""), data.get("goal_names", []))

    orchestrator.set_planner_event_hook(event_hook)


@click.group(name="plan")
def plan_group() -> None:
    """Strategic planning operations."""
    pass


@plan_group.command(name="init")
@click.option("--dry-run", is_flag=True, help="Use dry-run mode (no actual planning)")
@catch_domain_errors
def plan_init(dry_run: bool) -> None:
    """
    Start or resume discovery phase.

    Runs an interactive planning session to gather project requirements
    and create a project brief.
    """
    if dry_run:
        os.environ["AGENT_MODE"] = "dry-run"

    project = _require_project()
    ok(f"Project: {project}")

    container = AppContainer.from_env()
    repo = container.project_plan_repo
    plan = repo.get()

    if plan is not None:
        if plan.status != "discovery":
            warn(f"Plan is currently in '{plan.status}' state.")
            if not click.confirm("Start over and create a new plan?"):
                return
            plan = None

    try:
        orchestrator = container.planner_orchestrator
    except Exception as exc:
        exc_name = type(exc).__name__
        if "SpecNotFound" in exc_name or "NotFound" in exc_name:
            die(
                f"No project spec found for project '{project}'.\n"
                "  Run: orchestrator init\n"
                "  to create a project spec before running `plan init`."
            )
        raise

    planner_log, session_id = _make_planner_logger(container, "discovery")
    _bind_planner_hooks(orchestrator, planner_log)
    planner_log.session_start()
    _start = time.monotonic()

    try:
        with _spinner("Running discovery session"):
            result = orchestrator.start_discovery(io_handler=_io_handler)

        if result.failure_reason:
            planner_log.session_end(success=False)
            die(f"Discovery failed: {result.failure_reason}")
            return

        planner_log.session_end(success=True)
    finally:
        planner_log.close()

    if result.brief:
        _print_section("PROJECT BRIEF")
        info(f"Vision: {result.brief.vision}")
        if result.brief.constraints:
            info("Constraints:")
            for c in result.brief.constraints:
                info(f"  - {c}")
        if result.brief.phase_1_exit_criteria:
            info(f"Phase 1 exit criteria: {result.brief.phase_1_exit_criteria}")
        if result.brief.open_questions:
            info("Open questions:")
            for q in result.brief.open_questions:
                info(f"  - {q}")

        if click.confirm("\nApprove this project brief?"):
            plan = orchestrator.approve_brief()
            ok(f"Brief approved — status: {plan.status.value}")
            info("Run: orchestrator plan architect")


@plan_group.command(name="architect")
@click.option("--dry-run", is_flag=True, help="Use dry-run mode")
@catch_domain_errors
def plan_architect(dry_run: bool) -> None:
    """
    Run architecture planning phase.

    Proposes architectural decisions and phase plan for approval.
    """
    if dry_run:
        os.environ["AGENT_MODE"] = "dry-run"

    project = _require_project()
    ok(f"Project: {project}")

    container = AppContainer.from_env()
    repo = container.project_plan_repo
    plan = repo.get()

    if plan is None or plan.status != "architecture":
        if plan:
            warn(f"Plan is in '{plan.status}' state (expected 'architecture').")
        else:
            warn("No plan found. Run 'orchestrator plan init' first.")
        return

    orchestrator = container.planner_orchestrator

    planner_log, session_id = _make_planner_logger(container, "architecture")
    _bind_planner_hooks(orchestrator, planner_log)
    planner_log.session_start()
    _start = time.monotonic()

    try:
        with _spinner("Running architecture planning"):
            result = orchestrator.run_architecture(io_handler=_io_handler)

        if result.failure_reason:
            planner_log.session_end(success=False)
            die(f"Architecture planning failed: {result.failure_reason}")
            return

        planner_log.session_end(success=True)
    finally:
        planner_log.close()

    if result.pending_decisions:
        _print_section("PROPOSED DECISIONS")
        for i, decision in enumerate(result.pending_decisions, 1):
            info(f"{i}. [{decision.id}] {decision.domain}")
            info(f"   Date: {decision.date}")
            for line in decision.content.splitlines():
                info(f"   {line}")
            if decision.spec_changes and not decision.spec_changes.is_empty:
                sc = decision.spec_changes
                info("   Spec changes:")
                if sc.add_required:
                    info(f"     Add required: {', '.join(sc.add_required)}")
                if sc.add_forbidden:
                    info(f"     Add forbidden: {', '.join(sc.add_forbidden)}")
                if sc.remove_required:
                    info(f"     Remove required: {', '.join(sc.remove_required)}")
                if sc.remove_forbidden:
                    info(f"     Remove forbidden: {', '.join(sc.remove_forbidden)}")

    if result.pending_phases:
        _print_section("PROPOSED PHASES")
        for phase in result.pending_phases:
            info(f"Phase {phase.index}: {phase.name}")
            info(f"  Goal: {phase.goal}")
            if phase.goal_names:
                for gn in phase.goal_names:
                    info(f"    • {gn}")
            if phase.exit_criteria:
                info(f"  Exit criteria: {phase.exit_criteria}")

    approved_ids = []
    if result.pending_decisions:
        _print_section("DECISION APPROVAL")
        for decision in result.pending_decisions:
            action = click.prompt(
                f"\nApprove decision '{decision.id}'? (y/n/edit)",
                type=click.Choice(["y", "n", "edit"], case_sensitive=False),
                default="y",
            )
            if action.lower() == "y":
                approved_ids.append(decision.id)
                ok(f"✓ {decision.id}")
            elif action.lower() == "edit":
                with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
                    f.write(decision.content)
                    temp_path = f.name
                try:
                    editor = os.environ.get("EDITOR", "vim")
                    os.system(f"{editor} {temp_path}")
                    info(f"Edited decision: {decision.id}")
                finally:
                    os.unlink(temp_path)
            else:
                warn(f"✗ {decision.id}")

        info(f"\nApproved {len(approved_ids)} decisions.")

    if click.confirm("\nApprove phase plan and start execution?"):
        approval = orchestrator.approve_architecture(approved_ids)
        elapsed = time.monotonic() - _start
        _print_section("ARCHITECTURE APPROVED")
        ok(f"Architecture approved in {elapsed:.1f}s")
        ok(f"  Decisions applied : {approval.decisions_applied}")
        ok(f"  Spec changes      : {approval.spec_changes_applied}")
        ok(f"  Goals dispatched  : {len(approval.goals_dispatched)}")
        for gid in approval.goals_dispatched:
            info(f"    → {gid}")


@plan_group.command(name="review")
@click.option("--dry-run", is_flag=True, help="Use dry-run mode")
@catch_domain_errors
def plan_review(dry_run: bool) -> None:
    """
    Run phase review phase.

    Review completed phase and plan the next phase.
    """
    if dry_run:
        os.environ["AGENT_MODE"] = "dry-run"

    project = _require_project()
    ok(f"Project: {project}")

    container = AppContainer.from_env()
    repo = container.project_plan_repo
    plan = repo.get()

    if plan is None or plan.status != "phase_review":
        if plan:
            warn(f"Plan is in '{plan.status}' state (expected 'phase_review').")
        else:
            warn("No plan found. Run 'orchestrator plan init' first.")
        return

    orchestrator = container.planner_orchestrator

    planner_log, session_id = _make_planner_logger(container, "phase_review")
    _bind_planner_hooks(orchestrator, planner_log)
    planner_log.session_start()

    try:
        with _spinner("Running phase review"):
            result = orchestrator.run_phase_review(io_handler=_io_handler)

        if result.failure_reason:
            planner_log.session_end(success=False)
            die(f"Phase review failed: {result.failure_reason}")
            return

        planner_log.session_end(success=True)
    finally:
        planner_log.close()

    if result.lessons:
        _print_section("LESSONS LEARNED")
        for line in result.lessons.splitlines():
            info(line)

    if result.next_phase_proposal:
        _print_section("NEXT PHASE PROPOSAL")
        phase = result.next_phase_proposal
        info(f"Phase {phase.index}: {phase.name}")
        info(f"  Goal: {phase.goal}")
        if phase.exit_criteria:
            info(f"  Exit criteria: {phase.exit_criteria}")

    if result.pending_decisions:
        _print_section("PENDING DECISIONS")
        for decision in result.pending_decisions:
            info(f"\n[{decision.id}] {decision.domain}")
            for line in decision.content.splitlines():
                info(f"  {line}")

    approved_ids = []
    if result.pending_decisions:
        for decision in result.pending_decisions:
            if click.confirm(f"\nApprove decision '{decision.id}'?"):
                approved_ids.append(decision.id)
                ok(f"✓ {decision.id}")
            else:
                warn(f"✗ {decision.id}")

    approve_next = click.confirm("\nContinue with next phase?")

    if approve_next or click.confirm("Mark project as done?"):
        approval = orchestrator.approve_phase_review(approve_next=approve_next)
        _print_section("PHASE REVIEW APPROVED")
        ok(f"Phase review approved!")
        ok(f"  Plan status       : {approval.plan_status}")
        ok(f"  Decisions applied : {approval.decisions_applied}")
        ok(f"  Goals dispatched  : {len(approval.goals_dispatched)}")
        for gid in approval.goals_dispatched:
            info(f"    → {gid}")


@plan_group.command(name="status")
@catch_domain_errors
def plan_status() -> None:
    """
    Show current project plan status.
    """
    project = _require_project()
    container = AppContainer.from_env()
    orchestrator = container.planner_orchestrator
    plan = orchestrator.get_status()

    _print_section(f"PROJECT PLAN — {project}")
    info(f"Status : {plan.status.value}")
    if plan.vision:
        info(f"Vision : {plan.vision}")

    if plan.phases:
        info("")
        info("Phases:")
        for phase in plan.phases:
            status_icon = {
                "planned": "○",
                "active": "●",
                "completed": "✓",
            }.get(phase.status.value, "?")
            label = f"Phase {phase.index}: {phase.name}"
            if phase.status.value == "active":
                label = f"{label}  ← now"
            goal_count = len(phase.goal_names)
            info(f"  {status_icon}  {label}  ({goal_count} goals)")

    try:
        goal_repo = container.goal_repo
        all_goals = goal_repo.list_all() if hasattr(goal_repo, "list_all") else []
        active_goals = [g for g in all_goals if g.status.value not in ("merged", "cancelled")]
        if active_goals:
            info("")
            info("Active Goals:")
            for goal in active_goals:
                task_count = len(goal.tasks) if hasattr(goal, "tasks") and goal.tasks else 0
                status_icon = "●" if goal.status.value == "running" else "○"
                info(f"  {status_icon}  {goal.name:<28} {goal.status.value:<12} {task_count} tasks")
    except Exception:
        pass

    info("")
    info("JIT Planner: enabled")


@plan_group.command(name="decision")
@click.argument("description")
@click.option("--dry-run", is_flag=True, help="Use dry-run mode")
def plan_decision(description: str, dry_run: bool) -> None:
    """
    Surface a mid-phase architectural question.

    Runs a short architecture-mode session on just the question,
    shows proposed decision for approval.
    """
    if dry_run:
        os.environ["AGENT_MODE"] = "dry-run"

    info(f"Decision description: {description}")
    info("(Decision approval flow not fully implemented in this prototype)")


@plan_group.command(name="logs")
@click.option("--session-id", default=None, help="Specific session ID to display")
@click.option(
    "--filter",
    "event_filter",
    default=None,
    type=click.Choice(["turns", "tools", "decisions", "phases", "all"]),
    help="Filter event types (default: all)",
)
@click.option("--tail", default=0, help="Show only the last N lines")
def plan_logs(session_id: str, event_filter: str, tail: int) -> None:
    """
    Display or tail the planning session log.

    Reads the JSONL log file written by PlannerLiveLogger and renders
    it with terminal colours, filtered by event type if requested.

    Examples:
        orchestrator plan logs
        orchestrator plan logs --filter turns
        orchestrator plan logs --filter tools --tail 20
        orchestrator plan logs --session-id abc123
    """
    import json

    from src.infra.logging.live_logger import LiveLogger
    from src.infra.logging.log_events import LogEvent, LogEventType

    container = AppContainer.from_env()
    log_dir = container.paths.logs_dir / "planner"

    if not log_dir.exists():
        die("No planner session logs found. Run a planning command first.")
        return

    jsonl_files = sorted(log_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not jsonl_files:
        die("No planner session logs found. Run a planning command first.")
        return

    if session_id:
        matched = [f for f in jsonl_files if session_id in f.name]
        if not matched:
            die(f"No log file found for session-id '{session_id}'.")
            return
        log_file = matched[-1]
    else:
        log_file = jsonl_files[-1]

    events: list = []
    with open(log_file, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                events.append(LogEvent.from_json(data))
            except Exception:
                continue

    FILTER_MAP = {
        "turns": {LogEventType.PLANNER_TURN},
        "tools": {LogEventType.PLANNER_TOOL_CALL, LogEventType.PLANNER_TOOL_RESULT},
        "decisions": {LogEventType.PLANNER_DECISION},
        "phases": {LogEventType.PLANNER_PHASE},
    }
    if event_filter and event_filter != "all":
        allowed = FILTER_MAP.get(event_filter, set())
        events = [e for e in events if e.event_type in allowed]

    if tail > 0:
        events = events[-tail:]

    if not events:
        suffix = f" for filter '{event_filter}'" if event_filter else ""
        info(f"No events found in {log_file.name}{suffix}.")
        return

    info(f"Log: {log_file.name}  ({len(events)} events)")
    click.echo()

    live = LiveLogger()
    live.register_agent("planner", "replay", str(log_dir))
    for event in events:
        live._render_to_terminal(event)
    live.close()


def register_cli(main_group) -> None:
    """Register this group with the main CLI."""
    main_group.add_command(plan_group)
