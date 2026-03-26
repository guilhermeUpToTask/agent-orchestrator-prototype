"""
src/infra/cli/wizard/ — Interactive setup wizard.

Steps:
  1. Orchestrator config  — project_name, redis_url  → .orchestrator/config.json
  2. Dependency check     — redis, git, runtimes
  3. Project settings     — source_repo_url          → projects/<n>/project.json
  4. Project spec         — tech stack, constraints  → projects/<n>/project_spec.yaml
  5. Agent registry       — register first agent (optional)
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import click

from src.infra.cli.wizard.steps.config   import collect_orchestrator_config, collect_project_settings
from src.infra.cli.wizard.steps.deps     import check_and_report
from src.infra.cli.wizard.steps.github   import collect_and_setup_github
from src.infra.cli.wizard.steps.registry import setup_registry
from src.infra.cli.wizard.steps.spec     import collect_and_write_spec
from src.infra.config_manager import OrchestratorConfigManager


def run_wizard(
    home: Path | None = None,
    *,
    registry_factory: Callable | None = None,
    skip_spec: bool = False,
    github_only: bool = False,
) -> bool:
    """
    Run the interactive setup wizard.

    Parameters
    ----------
    home         : Override for the orchestrator home directory (default: ~/.orchestrator).
                   Used in tests. In production this should always be None.
    skip_spec    : Skip the ProjectSpec step (Step 4).
    github_only  : Re-run only Step 6 (GitHub Setup). Useful when adding
                   GitHub integration to an existing project, or after
                   updating ci.required_checks in the spec.

    Returns True on success, False on failure/abort.
    """
    total_steps = 6 if not skip_spec else 5
    manager = OrchestratorConfigManager(home)

    _print_banner()

    # ------------------------------------------------------------------
    # Step 1 — Orchestrator configuration (global, per-machine)
    # ------------------------------------------------------------------
    click.echo(_section(f"Step 1 of {total_steps} — Orchestrator Configuration"))
    click.echo("  These settings apply to this machine, not just this project.\n")
    orch_data = collect_orchestrator_config(manager)
    click.echo()

    # Persist orchestrator config IMMEDIATELY after Step 1 so it is never
    # lost — even if the dep check or later steps fail.  Re-running `init`
    # will pick up these values as defaults.
    manager.save(orch_data)
    click.echo(f"  ✓ Orchestrator config written → {manager.config_path}")
    click.echo(f"  ✓ Active project: {orch_data['project_name']}")

    # ------------------------------------------------------------------
    # Step 2 — Dependency check
    # ------------------------------------------------------------------
    click.echo(_section(f"Step 2 of {total_steps} — Dependency Check"))
    if not check_and_report(orch_data["redis_url"]):
        click.echo(
            "\n⚠  Fix the issues above then re-run:  orchestrator init\n"
            "   Your config has been saved — project and Redis URL are already set.\n",
            err=True,
        )
        return False

    # ------------------------------------------------------------------
    # Step 3 — Project settings (per-project operational config)
    # ------------------------------------------------------------------
    click.echo(_section(f"Step 3 of {total_steps} — Project Settings"))
    click.echo("  These settings belong to this project, not the machine.\n")

    from src.infra.config import OrchestratorConfig
    orch_cfg = OrchestratorConfig(**orch_data)   # ephemeral — just for path resolution
    project_settings_data = collect_project_settings(
        project_name=orch_data["project_name"],
        orchestrator_home=orch_cfg.orchestrator_home,
    )

    # Persist project settings
    from src.infra.project_settings import ProjectSettings, ProjectSettingsManager
    project_home = orch_cfg.orchestrator_home / "projects" / orch_data["project_name"]
    ps_manager = ProjectSettingsManager(project_home)
    ps_manager.save(ProjectSettings(**project_settings_data))
    click.echo(f"  ✓ Project settings written → {ps_manager.settings_path}")

    # ------------------------------------------------------------------
    # Step 4 — Project spec (domain/architecture constraints)
    # ------------------------------------------------------------------
    if not skip_spec:
        click.echo(_section(f"Step 4 of {total_steps} — Project Specification"))
        spec_ok = collect_and_write_spec({"project_name": orch_data["project_name"]})
        if not spec_ok:
            click.echo(
                "\n⚠  Project spec was not written. "
                "Run  orchestrate spec init  to create it later.\n"
            )

    # ------------------------------------------------------------------
    # Step 5 — Agent registry
    # ------------------------------------------------------------------
    click.echo(_section(f"Step 5 of {total_steps} — Agent Registry"))
    setup_registry(orch_data, registry_factory)

    # ------------------------------------------------------------------
    # Step 6 — GitHub integration + project CI template
    # ------------------------------------------------------------------
    if not skip_spec:
        click.echo(_section(f"Step 6 of {total_steps} — GitHub Setup"))
        collect_and_setup_github({
            "project_name":    orch_data["project_name"],
            "orchestrator_home": str(orch_cfg.orchestrator_home),
        })

    click.echo(f"\n✓  Setup complete! Active project: {orch_data['project_name']}")
    click.echo("\n  Useful next steps:")
    click.echo(f"    orchestrator project status          — confirm active project")
    click.echo(f"    orchestrator project list            — see all projects")
    click.echo(f"    orchestrator project use <n>      — switch project")
    click.echo(f"    orchestrator plan init               — start planning")
    click.echo(f"    orchestrator system start            — boot daemons\n")
    return True


def _print_banner() -> None:
    click.echo()
    click.echo("┌──────────────────────────────────────────────────┐")
    click.echo("│     Agent Orchestrator  —  Setup Wizard          │")
    click.echo("└──────────────────────────────────────────────────┘")
    click.echo()


def _section(title: str) -> str:
    line = "─" * (len(title) + 4)
    return f"\n  ── {title} {line}"
