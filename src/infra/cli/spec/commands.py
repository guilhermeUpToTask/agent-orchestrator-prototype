"""
src/infra/cli/spec/commands.py — Project spec management commands.

Commands:
  orchestrate spec show      — print the active spec
  orchestrate spec init      — create project_spec.yaml interactively (outside wizard)
  orchestrate spec validate  — validate a task description or dependency against spec
  orchestrate spec propose   — stage a change for operator review
  orchestrate spec diff      — show the pending proposal vs. the live spec
  orchestrate spec apply     — promote a pending proposal to the live spec

These commands are the only approved path for humans to modify project_spec.yaml.
Agents must go through ProposeSpecChange (which writes only .proposed.yaml).
"""
from __future__ import annotations

import sys

import click

from src.infra.cli.error_handler import catch_domain_errors, die, ok


@click.group("spec")
def spec_group():
    """Manage the project specification (tech stack, constraints, structure)."""


# ---------------------------------------------------------------------------
# spec show
# ---------------------------------------------------------------------------

@spec_group.command("show")
@catch_domain_errors
def spec_show():
    """
    Print the active project_spec.yaml in a human-readable format.

    Example:
      orchestrate spec show
    """
    from src.infra.factory import build_load_project_spec
    from src.infra.config import config as app_config

    spec = build_load_project_spec().execute(app_config.project_name)
    ac = spec.get_architecture_constraints()

    click.echo(f"\n  Project:  {ac['project']}")
    click.echo(f"  Version:  {ac['version']}")
    click.echo(f"  Domain:   {ac['domain']}")

    click.echo("\n  Tech stack:")
    for tier, items in ac["tech_stack"].items():
        if items:
            click.echo(f"    {tier:<10} {', '.join(items)}")

    if ac["constraints"]["forbidden"]:
        click.echo("\n  Forbidden:  " + ", ".join(ac["constraints"]["forbidden"]))
    if ac["constraints"]["required"]:
        click.echo("  Required:   " + ", ".join(ac["constraints"]["required"]))

    if ac["structure"]:
        click.echo("\n  Structure:")
        for d in ac["structure"]:
            click.echo(f"    {d['name']:<30} {d['purpose']}")

    click.echo()


# ---------------------------------------------------------------------------
# spec init  (run outside the wizard — e.g. when spec was skipped)
# ---------------------------------------------------------------------------

@spec_group.command("init")
@click.option(
    "--overwrite",
    is_flag=True,
    default=False,
    help="Overwrite an existing project_spec.yaml without prompting.",
)
@catch_domain_errors
def spec_init(overwrite: bool):
    """
    Interactively create project_spec.yaml for the active project.

    This runs the same spec step as the setup wizard. Use it when you
    skipped the spec during `orchestrate init` or when setting up a project
    in a non-interactive environment.

    Example:
      orchestrate spec init
      orchestrate spec init --overwrite
    """
    from src.infra.config import config as app_config
    from src.infra.fs.project_spec_repository import FileProjectSpecRepository

    repo = FileProjectSpecRepository(
        orchestrator_home=app_config.orchestrator_home
    )

    if repo.exists(app_config.project_name) and not overwrite:
        click.echo(
            f"  project_spec.yaml already exists for '{app_config.project_name}'.\n"
            "  Use --overwrite to replace it, or  orchestrate spec propose  "
            "to stage a change."
        )
        return

    from src.infra.cli.wizard.steps.spec import collect_and_write_spec

    success = collect_and_write_spec({"project_name": app_config.project_name})
    if not success:
        die("Spec creation failed.")
    ok(f"Project spec created for '{app_config.project_name}'")


# ---------------------------------------------------------------------------
# spec validate
# ---------------------------------------------------------------------------

@spec_group.command("validate")
@click.option("--description", "-d", default="", help="Task description to validate.")
@click.option(
    "--dep",
    "dependencies",
    multiple=True,
    help="Dependency name to check (repeatable: --dep pydantic --dep redis).",
)
@click.option(
    "--dir",
    "directories",
    multiple=True,
    help="Directory path to check (repeatable: --dir src/domain).",
)
@catch_domain_errors
def spec_validate(description: str, dependencies: tuple, directories: tuple):
    """
    Validate a task description, dependencies, or directory paths against
    the active project spec.

    Examples:
      orchestrate spec validate --description "use django ORM"
      orchestrate spec validate --dep django --dep flask
      orchestrate spec validate --dir src/legacy
      orchestrate spec validate -d "add redis cache" --dep redis --dir src/infra
    """
    from src.infra.factory import build_validate_against_spec
    from src.domain.project_spec.errors import SpecNotFoundError

    try:
        uc = build_validate_against_spec()
    except SpecNotFoundError as exc:
        die(str(exc))
        return  # unreachable, die() exits

    result = uc.execute(
        task_description=description,
        dependencies=list(dependencies),
        directories=list(directories),
    )

    if result.passed:
        ok("Validation passed — no constraint violations found.")
    else:
        click.echo("\n  ✗  Constraint violations:\n", err=True)
        for v in result.violations:
            click.echo(f"    • {v}", err=True)

    if result.warnings:
        click.echo("\n  ⚠  Warnings:\n")
        for w in result.warnings:
            click.echo(f"    • {w}")

    if not result.passed:
        sys.exit(1)


# ---------------------------------------------------------------------------
# spec propose
# ---------------------------------------------------------------------------

@spec_group.command("propose")
@click.option("--bump-version", type=click.Choice(["patch", "minor", "major"]), default=None)
@click.option("--add-forbidden", multiple=True, help="Add a forbidden pattern.")
@click.option("--remove-forbidden", multiple=True, help="Remove a forbidden pattern.")
@click.option("--add-required", multiple=True, help="Add a required pattern.")
@click.option("--remove-required", multiple=True, help="Remove a required pattern.")
@click.option("--rationale", "-r", default="", help="Human-readable reason for the change.")
@catch_domain_errors
def spec_propose(
    bump_version: str | None,
    add_forbidden: tuple,
    remove_forbidden: tuple,
    add_required: tuple,
    remove_required: tuple,
    rationale: str,
):
    """
    Stage a change proposal for operator review.

    Writes project_spec.proposed.yaml — the live spec is NOT modified.
    An operator must run  orchestrate spec apply  to promote the proposal.

    Examples:
      orchestrate spec propose --add-forbidden celery --rationale "use Redis Streams"
      orchestrate spec propose --bump-version minor
      orchestrate spec propose --remove-forbidden flask --add-required fastapi
    """
    from src.infra.factory import build_propose_spec_change
    from src.infra.config import config as app_config
    from src.app.usecases.propose_spec_change import ChangeProposal

    # Resolve new version string from bump type
    new_version: str | None = None
    if bump_version is not None:
        from src.infra.factory import build_load_project_spec
        spec = build_load_project_spec().execute(app_config.project_name)
        v = spec.version
        if bump_version == "patch":
            new_version = v.bump_patch().raw
        elif bump_version == "minor":
            new_version = v.bump_minor().raw
        else:
            new_version = v.bump_major().raw

    proposal = ChangeProposal(
        new_version=new_version,
        add_forbidden=list(add_forbidden),
        remove_forbidden=list(remove_forbidden),
        add_required=list(add_required),
        remove_required=list(remove_required),
        rationale=rationale,
    )

    result = build_propose_spec_change().execute(app_config.project_name, proposal)

    if result.accepted:
        ok(f"Proposal staged → {result.proposal_path}")
        click.echo(
            "  Review: orchestrate spec diff\n"
            "  Apply:  orchestrate spec apply"
        )
    else:
        die(f"Proposal rejected: {result.rejection_reason}")


# ---------------------------------------------------------------------------
# spec diff
# ---------------------------------------------------------------------------

@spec_group.command("diff")
@catch_domain_errors
def spec_diff():
    """
    Show the differences between the live spec and the pending proposal.

    Prints a side-by-side YAML diff. If no proposal exists, says so.

    Example:
      orchestrate spec diff
    """
    import difflib
    from src.infra.config import config as app_config
    from src.infra.fs.project_spec_repository import FileProjectSpecRepository

    repo = FileProjectSpecRepository(orchestrator_home=app_config.orchestrator_home)
    proposed_path = (
        repo._spec_path(app_config.project_name).parent
        / "project_spec.proposed.yaml"
    )

    if not repo.exists(app_config.project_name):
        die("No project_spec.yaml found. Run  orchestrate spec init  first.")
        return

    if not proposed_path.exists():
        click.echo("  No pending proposal found.")
        click.echo("  Use  orchestrate spec propose  to stage a change.")
        return

    live_text = repo._spec_path(app_config.project_name).read_text().splitlines(keepends=True)
    proposed_text = proposed_path.read_text().splitlines(keepends=True)

    diff = list(
        difflib.unified_diff(
            live_text,
            proposed_text,
            fromfile="project_spec.yaml (live)",
            tofile="project_spec.proposed.yaml",
        )
    )

    if not diff:
        click.echo("  Proposal is identical to the live spec (no changes).")
        return

    for line in diff:
        if line.startswith("+"):
            click.echo(click.style(line, fg="green"), nl=False)
        elif line.startswith("-"):
            click.echo(click.style(line, fg="red"), nl=False)
        else:
            click.echo(line, nl=False)
    click.echo()


# ---------------------------------------------------------------------------
# spec apply
# ---------------------------------------------------------------------------

@spec_group.command("apply")
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip confirmation.")
@catch_domain_errors
def spec_apply(yes: bool):
    """
    Promote the pending proposal to the live project_spec.yaml.

    This is the operator approval gate. After running this command the
    proposed spec becomes the active spec and the .proposed.yaml is removed.

    Example:
      orchestrate spec apply
      orchestrate spec apply --yes
    """
    from src.infra.config import config as app_config
    from src.infra.fs.project_spec_repository import FileProjectSpecRepository
    from src.domain.project_spec.errors import SpecValidationError

    repo = FileProjectSpecRepository(orchestrator_home=app_config.orchestrator_home)
    proposed_path = (
        repo._spec_path(app_config.project_name).parent
        / "project_spec.proposed.yaml"
    )

    if not proposed_path.exists():
        die(
            "No pending proposal found. "
            "Use  orchestrate spec propose  to stage a change first."
        )
        return

    # Load and validate the proposed spec before promoting it
    try:
        import yaml as _yaml
        raw = _yaml.safe_load(proposed_path.read_text())
        # Strip the rationale comment key — not part of the schema
        raw.pop("_proposal_rationale", None)
        from src.domain.project_spec.aggregate import ProjectSpec
        proposed_spec = ProjectSpec.from_dict(raw)
    except (SpecValidationError, ValueError) as exc:
        die(f"Proposed spec is invalid and cannot be applied: {exc}")
        return

    if not yes:
        click.echo(
            f"\n  Promoting proposal to live spec for '{app_config.project_name}':\n"
            f"    version: {proposed_spec.meta.version}\n"
            f"    domain:  {proposed_spec.objective.domain}\n"
        )
        click.confirm("  Apply this proposal?", abort=True)

    repo.save(proposed_spec)
    proposed_path.unlink()

    ok(
        f"Spec updated to v{proposed_spec.meta.version} "
        f"for project '{app_config.project_name}'"
    )
