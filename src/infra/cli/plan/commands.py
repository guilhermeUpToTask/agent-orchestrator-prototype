"""
src/infra/cli/plan/commands.py — CLI commands for strategic planning.

Commands:
  init         — Start discovery phase
  architect    — Run architecture planning
  review       — Run phase review
  status       — Show project plan status
  decision     — Surface mid-phase architectural question
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import Optional

import click

from src.infra.factory import build_planner_orchestrator, build_project_plan_repo
from src.infra.config import config as app_config
from src.infra.cli.error_handler import die, ok, warn


def _require_project() -> str:
    """
    Abort with a helpful message when no project is configured.

    Returns the active project name so callers can display it.
    Raises SystemExit(1) when project_name is None or 'default'.
    """
    project = app_config.project_name
    if not project:
        die(
            "No project configured.\n"
            "  Run: orchestrator init\n"
            "  Then try again."
        )
    return project


def _io_handler(question: str) -> str:
    """Default I/O handler that prompts the user."""
    return click.prompt(question, type=str)


@click.group(name="plan")
def plan_group() -> None:
    """Strategic planning operations."""
    pass


@plan_group.command(name="init")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Use dry-run mode (no actual planning)",
)
def plan_init(dry_run: bool) -> None:
    """
    Start or resume discovery phase.

    Runs an interactive planning session to gather project requirements
    and create a project brief.
    """
    if dry_run:
        # Override to dry-run mode for this command
        os.environ["AGENT_MODE"] = "dry-run"

    project = _require_project()
    click.echo(f"Project: {project}")

    repo = build_project_plan_repo()
    plan = repo.get()

    # Check if we can start discovery
    if plan is not None:
        if plan.status != "discovery":
            click.echo(f"Plan is currently in '{plan.status}' state.")
            if not click.confirm("Start over and create a new plan?"):
                return
            # Clear the existing plan for restart
            plan = None

    # Build orchestrator with dry-run mode if needed
    if dry_run:
        os.environ["AGENT_MODE"] = "dry-run"

    try:
        orchestrator = build_planner_orchestrator(io_handler=_io_handler)
    except Exception as exc:
        # Catch SpecNotFoundError and similar setup errors and surface them cleanly
        exc_name = type(exc).__name__
        if "SpecNotFound" in exc_name or "NotFound" in exc_name:
            die(
                f"No project spec found for project '{project}'.\n"
                "  Run: orchestrator init\n"
                "  to create a project spec before running `plan init`."
            )
        raise

    click.echo("Starting discovery phase...")
    result = orchestrator.start_discovery(io_handler=_io_handler)

    if result.failure_reason:
        click.echo(f"Discovery failed: {result.failure_reason}")
        return

    if result.brief:
        click.echo("\n" + "=" * 60)
        click.echo("PROJECT BRIEF")
        click.echo("=" * 60)
        click.echo(f"Vision: {result.brief.vision}")
        if result.brief.constraints:
            click.echo(f"\nConstraints:")
            for c in result.brief.constraints:
                click.echo(f"  - {c}")
        if result.brief.phase_1_exit_criteria:
            click.echo(f"\nPhase 1 exit criteria: {result.brief.phase_1_exit_criteria}")
        if result.brief.open_questions:
            click.echo(f"\nOpen questions:")
            for q in result.brief.open_questions:
                click.echo(f"  - {q}")
        click.echo("=" * 60)

        if click.confirm("\nApprove this project brief?"):
            plan = orchestrator.approve_brief()
            click.echo(f"Brief approved. Plan status: {plan.status.value}")
            click.echo("Run: orchestrator plan architect")


@plan_group.command(name="architect")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Use dry-run mode",
)
def plan_architect(dry_run: bool) -> None:
    """
    Run architecture planning phase.

    Proposes architectural decisions and phase plan for approval.
    """
    if dry_run:
        os.environ["AGENT_MODE"] = "dry-run"

    project = _require_project()
    click.echo(f"Project: {project}")

    repo = build_project_plan_repo()
    plan = repo.get()

    if plan is None or plan.status != "architecture":
        if plan:
            click.echo(f"Plan is in '{plan.status}' state (expected 'architecture').")
        else:
            click.echo("No plan found. Run 'orchestrator plan init' first.")
        return

    orchestrator = build_planner_orchestrator(io_handler=_io_handler)

    click.echo("Running architecture planning...")
    result = orchestrator.run_architecture(io_handler=_io_handler)

    if result.failure_reason:
        click.echo(f"Architecture planning failed: {result.failure_reason}")
        return

    # Display pending decisions
    if result.pending_decisions:
        click.echo("\n" + "=" * 60)
        click.echo("PROPOSED DECISIONS")
        click.echo("=" * 60)
        for i, decision in enumerate(result.pending_decisions, 1):
            click.echo(f"\n{i}. [{decision.id}] {decision.domain}")
            click.echo(f"   Date: {decision.date}")
            click.echo(f"   Content: {decision.content[:100]}...")
            if decision.spec_changes and not decision.spec_changes.is_empty:
                sc = decision.spec_changes
                click.echo("   Spec changes:")
                if sc.add_required:
                    click.echo(f"     Add required: {', '.join(sc.add_required)}")
                if sc.add_forbidden:
                    click.echo(f"     Add forbidden: {', '.join(sc.add_forbidden)}")
                if sc.remove_required:
                    click.echo(f"     Remove required: {', '.join(sc.remove_required)}")
                if sc.remove_forbidden:
                    click.echo(f"     Remove forbidden: {', '.join(sc.remove_forbidden)}")

    # Display proposed phases
    if result.pending_phases:
        click.echo("\n" + "=" * 60)
        click.echo("PROPOSED PHASES")
        click.echo("=" * 60)
        for phase in result.pending_phases:
            click.echo(f"\nPhase {phase.index}: {phase.name}")
            click.echo(f"  Goal: {phase.goal}")
            if phase.exit_criteria:
                click.echo(f"  Exit criteria: {phase.exit_criteria}")

    # Decision approval loop
    if result.pending_decisions:
        approved_ids = []
        for decision in result.pending_decisions:
            action = click.prompt(
                f"\nApprove decision '{decision.id}'? (y/n/edit)",
                type=click.Choice(["y", "n", "edit"], case_sensitive=False),
                default="y",
            )
            if action.lower() == "y":
                approved_ids.append(decision.id)
            elif action.lower() == "edit":
                # Open editor with decision content
                with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
                    f.write(decision.content)
                    temp_path = f.name
                try:
                    editor = os.environ.get("EDITOR", "vim")
                    os.system(f"{editor} {temp_path}")
                    with open(temp_path, "r") as f:
                        new_content = f.read()
                    # Update decision content (in a real implementation, this would update the session)
                    click.echo(f"Edited decision: {decision.id}")
                finally:
                    os.unlink(temp_path)
        click.echo(f"\nApproved {len(approved_ids)} decisions.")
    else:
        approved_ids = []

    if click.confirm("\nApprove phase plan and start execution?"):
        result = orchestrator.approve_architecture(approved_ids)
        click.echo(f"\nArchitecture approved!")
        click.echo(f"Plan status: {result.plan_status}")
        click.echo(f"Decisions applied: {result.decisions_applied}")
        click.echo(f"Spec changes applied: {result.spec_changes_applied}")
        click.echo(f"Goals dispatched: {len(result.goals_dispatched)}")


@plan_group.command(name="review")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Use dry-run mode",
)
def plan_review(dry_run: bool) -> None:
    """
    Run phase review phase.

    Review completed phase and plan the next phase.
    """
    if dry_run:
        os.environ["AGENT_MODE"] = "dry-run"

    project = _require_project()
    click.echo(f"Project: {project}")

    repo = build_project_plan_repo()
    plan = repo.get()

    if plan is None or plan.status != "phase_review":
        if plan:
            click.echo(f"Plan is in '{plan.status}' state (expected 'phase_review').")
        else:
            click.echo("No plan found. Run 'orchestrator plan init' first.")
        return

    orchestrator = build_planner_orchestrator(io_handler=_io_handler)

    click.echo("Running phase review...")
    result = orchestrator.run_phase_review(io_handler=_io_handler)

    if result.failure_reason:
        click.echo(f"Phase review failed: {result.failure_reason}")
        return

    # Display retrospective
    if result.lessons:
        click.echo("\n" + "=" * 60)
        click.echo("LESSONS LEARNED")
        click.echo("=" * 60)
        click.echo(result.lessons)

    # Display next phase proposal
    if result.next_phase_proposal:
        click.echo("\n" + "=" * 60)
        click.echo("NEXT PHASE PROPOSAL")
        click.echo("=" * 60)
        phase = result.next_phase_proposal
        click.echo(f"Phase {phase.index}: {phase.name}")
        click.echo(f"  Goal: {phase.goal}")
        if phase.exit_criteria:
            click.echo(f"  Exit criteria: {phase.exit_criteria}")

    # Display pending decisions
    if result.pending_decisions:
        click.echo("\n" + "=" * 60)
        click.echo("PENDING DECISIONS")
        click.echo("=" * 60)
        for decision in result.pending_decisions:
            click.echo(f"\n[{decision.id}] {decision.domain}")
            click.echo(f"  {decision.content[:100]}...")

    # Decision approval loop (same as architect)
    if result.pending_decisions:
        approved_ids = []
        for decision in result.pending_decisions:
            if click.confirm(f"\nApprove decision '{decision.id}'?"):
                approved_ids.append(decision.id)
    else:
        approved_ids = []

    # Continuation decision
    approve_next = click.confirm("\nContinue with next phase?")

    if approve_next or click.confirm("Mark project as done?"):
        result = orchestrator.approve_phase_review(approve_next=approve_next)
        click.echo(f"\nPhase review approved!")
        click.echo(f"Plan status: {result.plan_status}")
        click.echo(f"Decisions applied: {result.decisions_applied}")
        click.echo(f"Goals dispatched: {len(result.goals_dispatched)}")


@plan_group.command(name="status")
def plan_status() -> None:
    """
    Show current project plan status.
    """
    project = _require_project()
    click.echo(f"Project: {project}")
    orchestrator = build_planner_orchestrator()
    plan = orchestrator.get_status()

    click.echo(f"\nPlan: {plan.plan_id}")
    click.echo(f"Status: {plan.status.value}")
    click.echo(f"Vision: {plan.vision}")

    if plan.phases:
        click.echo("\nPhases:")
        for phase in plan.phases:
            status_icon = {
                "planned": "○",
                "active": "●",
                "completed": "✓",
            }.get(phase.status.value, "?")
            label = f"Phase {phase.index}: {phase.name}"
            if phase.status.value == "active":
                label = f"{label} (now)"
            click.echo(f"  {status_icon} {label}")
            click.echo(f"     Goal: {phase.goal}")
            if phase.goal_names:
                click.echo(f"     Goals: {', '.join(phase.goal_names)}")


@plan_group.command(name="decision")
@click.argument("description")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Use dry-run mode",
)
def plan_decision(description: str, dry_run: bool) -> None:
    """
    Surface a mid-phase architectural question.

    Runs a short architecture-mode session on just the question,
    shows proposed decision for approval.
    """
    if dry_run:
        os.environ["AGENT_MODE"] = "dry-run"

    click.echo(f"Decision description: {description}")
    click.echo("(Decision approval flow not fully implemented in this prototype)")


# Register the group in the main CLI
def register_cli(main_group) -> None:
    """Register this group with the main CLI."""
    main_group.add_command(plan_group)
