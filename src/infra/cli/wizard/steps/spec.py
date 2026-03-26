"""
src/infra/cli/wizard/steps/spec.py — Wizard Step 4: ProjectSpec generation.

Prompts the user for the minimal spec fields required for v0 and writes
project_spec.yaml into the project directory.

If a project_spec.yaml already exists for the project the user is prompted
to overwrite it or skip. Skipping is safe — the existing spec is preserved.

Design constraint:
  This step writes the spec directly (initial creation).  All subsequent
  changes must go through ProposeSpecChange → operator approval. There is
  no conflict here because this step only runs once: during `orchestrate init`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import click


def collect_and_write_spec(config_data: dict[str, Any]) -> bool:
    """
    Interactively collect spec fields and write project_spec.yaml.

    Args:
      config_data: dict produced by the config step, containing at minimum
                   ``project_name`` and ``orchestrator_home`` (derived from
                   OrchestratorConfig if not present directly).

    Returns:
      True  — spec was written successfully.
      False — user skipped or an error occurred.
    """
    from src.infra.fs.project_spec_repository import FileProjectSpecRepository
    from src.domain.project_spec import ProjectSpec

    project_name: str = config_data["project_name"]

    # Derive orchestrator_home from config if not directly in config_data
    try:
        orchestrator_home = Path(config_data["orchestrator_home"])
    except KeyError:
        from src.infra.config import config as app_config
        orchestrator_home = Path(app_config.orchestrator_home)

    repo = FileProjectSpecRepository(orchestrator_home=orchestrator_home)

    # Check for existing spec
    if repo.exists(project_name):
        click.echo(
            f"\n  A project_spec.yaml already exists for '{project_name}'."
        )
        if not click.confirm("  Overwrite it?", default=False):
            click.echo("  ↳ Keeping existing spec.")
            return True  # not an error — wizard can proceed

    click.echo(
        "\n  The project spec captures your tech stack and architectural\n"
        "  constraints. Agents and the validator will respect these rules.\n"
        "  You can change them later via:  orchestrate spec propose\n"
    )

    # ------------------------------------------------------------------ #
    # Collect fields                                                       #
    # ------------------------------------------------------------------ #

    objective_description: str = click.prompt(
        "  Project objective (one sentence)",
        default=f"Orchestrate coding agents to work on {project_name}",
    )

    objective_domain: str = click.prompt(
        "  Domain (e.g. developer-tooling, fintech, e-commerce)",
        default="developer-tooling",
    )

    backend_raw: str = click.prompt(
        "  Backend technologies (comma-separated, e.g. python,fastapi)",
        default="python",
    )

    database_raw: str = click.prompt(
        "  Database / messaging (comma-separated, e.g. redis,postgres)",
        default="redis",
    )

    infra_raw: str = click.prompt(
        "  Infrastructure (comma-separated, e.g. docker,git)",
        default="docker,git",
    )

    forbidden_raw: str = click.prompt(
        "  Forbidden dependencies (comma-separated, leave blank for none)",
        default="",
    )

    required_raw: str = click.prompt(
        "  Required dependencies (comma-separated, leave blank for none)",
        default="",
    )

    # ------------------------------------------------------------------ #
    # Parse and build                                                      #
    # ------------------------------------------------------------------ #

    def _split(s: str) -> list[str]:
        return [x.strip() for x in s.split(",") if x.strip()]

    # Default directory rules that make sense for any orchestrated project
    default_dirs = [
        {"name": "src", "purpose": "Application source code"},
        {"name": "tests", "purpose": "Test suite"},
    ]

    try:
        spec = ProjectSpec.create(
            name=project_name,
            objective_description=objective_description,
            objective_domain=objective_domain,
            backend=_split(backend_raw),
            database=_split(database_raw),
            infra=_split(infra_raw),
            forbidden=_split(forbidden_raw),
            required=_split(required_raw),
            directories=default_dirs,
            version="0.1.0",
        )
    except ValueError as exc:
        click.echo(f"\n  ✗ Could not create spec: {exc}", err=True)
        return False

    # ------------------------------------------------------------------ #
    # Validate before saving                                              #
    # ------------------------------------------------------------------ #

    violations = spec.validate_structure()
    if violations:
        click.echo("\n  ✗ Spec has structural violations:", err=True)
        for v in violations:
            click.echo(f"    • {v}", err=True)
        return False

    # ------------------------------------------------------------------ #
    # Save                                                                #
    # ------------------------------------------------------------------ #

    try:
        repo.save(spec)
    except OSError as exc:
        click.echo(f"\n  ✗ Failed to write project_spec.yaml: {exc}", err=True)
        return False

    spec_path = repo._spec_path(project_name)
    click.echo(f"\n  ✓ project_spec.yaml written → {spec_path}")
    click.echo(f"    version: {spec.meta.version}")
    click.echo(f"    domain:  {spec.objective.domain}")
    if spec.constraints.forbidden:
        click.echo(f"    forbidden: {', '.join(spec.constraints.forbidden)}")

    return True
