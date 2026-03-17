"""
src/wizard.py — Interactive setup wizard for the Agent Orchestrator.

Run via:  python -m src.cli init

What the wizard does
--------------------
1. Collects project_name, source_repo_url, redis_url from the user.
2. Checks all dependencies (redis, git, runtimes) against those values.
   • Continues only when redis + git + at least one runtime are satisfied.
3. Writes .orchestrator/config.json.
4. Inspects the agent registry — if empty, offers to register a first agent
   interactively so `orchestrator start` can succeed immediately.

Design notes
------------
• Kept in its own module so cli.py stays lean — the wizard has a lot of
  interactive I/O that would dwarf the actual command routing.
• All side-effects are injected (registry factory, config manager) so the
  wizard is fully unit-testable without real filesystem or Redis.
• Each step is a separate method to make individual testing easy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import click

from src.dependency_checker import DependencyChecker, DependencyReport
from src.orchestrator_config_manager import DEFAULTS, OrchestratorConfigManager


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_wizard(
    cwd: Path | None = None,
    *,
    registry_factory: Callable | None = None,
) -> bool:
    """
    Run the interactive setup wizard.

    Parameters
    ----------
    cwd:
        Working directory used to locate .orchestrator/config.json.
        Defaults to Path.cwd().
    registry_factory:
        Callable that returns an AgentRegistryPort instance.  Injected so
        tests can supply a mock without touching the filesystem or Redis.
        Defaults to ``src.infra.factory.build_agent_registry``.

    Returns
    -------
    bool
        True if setup completed successfully, False if the user aborted or
        a required dependency was unavailable.
    """
    manager = OrchestratorConfigManager(cwd)

    _print_banner()

    # ── Step 1: collect config values ────────────────────────────────────────
    click.echo(_section("Step 1 of 3 — Project Configuration"))
    config_data = _collect_project_config(manager)
    click.echo()

    # ── Step 2: dependency check ──────────────────────────────────────────────
    click.echo(_section("Step 2 of 3 — Dependency Check"))
    ok = _check_and_report(config_data["redis_url"])
    if not ok:
        click.echo(
            "\n✗  Fix the issues above then re-run:  orchestrator init\n",
            err=True,
        )
        return False

    # ── Step 3: write config ──────────────────────────────────────────────────
    manager.save(config_data)
    click.echo(f"\n  ✓ Config written → {manager.config_path}")

    # ── Step 4: agent registry ────────────────────────────────────────────────
    click.echo(_section("Step 3 of 3 — Agent Registry"))
    _setup_registry(config_data, registry_factory)

    click.echo("\n✓  Setup complete!  Run:  orchestrator start\n")
    return True


# ---------------------------------------------------------------------------
# Step helpers
# ---------------------------------------------------------------------------


def _print_banner() -> None:
    click.echo()
    click.echo("┌──────────────────────────────────────────────────┐")
    click.echo("│     Agent Orchestrator  —  Setup Wizard          │")
    click.echo("└──────────────────────────────────────────────────┘")
    click.echo()


def _section(title: str) -> str:
    line = "─" * (len(title) + 4)
    return f"\n  ── {title} {line}"


def _collect_project_config(manager: OrchestratorConfigManager) -> dict[str, Any]:
    """Prompt the user for the three core config values and return them."""
    existing = manager.load()

    project_name: str = click.prompt(
        "  Project name",
        default=existing.get("project_name") or DEFAULTS["project_name"],
    )

    source_repo_url: str = click.prompt(
        "  Source repository URL  (blank → empty local repo)",
        default=existing.get("source_repo_url") or "",
    )

    redis_url: str = click.prompt(
        "  Redis URL",
        default=existing.get("redis_url") or DEFAULTS["redis_url"],
    )

    return {
        "project_name": project_name,
        "source_repo_url": source_repo_url.strip() or None,
        "redis_url": redis_url.strip(),
    }


def _check_and_report(redis_url: str) -> bool:
    """
    Run all dependency checks, print a status table, and return True only
    if the minimum requirements (redis + git + one runtime) are met.
    """
    checker = DependencyChecker(redis_url=redis_url)
    report: DependencyReport = checker.run()

    click.echo()
    for r in report.results:
        icon = "  ✓" if r.ok else "  ✗"
        click.echo(f"{icon}  {r.name:<20} {r.message}")
        if not r.ok and r.install_hint:
            click.echo(f"              → {r.install_hint}")

    click.echo()

    if not report.redis_ok:
        click.echo("  Redis is required. Start it, then re-run the wizard.", err=True)
        return False
    if not report.git_ok:
        click.echo("  git is required. Install it, then re-run the wizard.", err=True)
        return False
    if not report.any_runtime_ok:
        click.echo(
            "  At least one agent runtime (gemini-cli or claude-code) must be installed.",
            err=True,
        )
        return False

    click.echo("  All required dependencies satisfied ✓")
    return True


def _setup_registry(
    config_data: dict[str, Any],
    registry_factory: Callable | None,
) -> None:
    """
    Check the agent registry.  If it's empty, offer an interactive prompt to
    register a first agent.
    """
    if registry_factory is None:
        # Lazy import to avoid circular dependencies in tests
        import os

        os.environ.setdefault("PROJECT_NAME", config_data["project_name"])
        os.environ.setdefault("REDIS_URL", config_data["redis_url"])
        from src.infra.factory import build_agent_registry  # noqa: PLC0415

        registry_factory = build_agent_registry

    try:
        registry = registry_factory()
        agents = registry.list_agents()
    except Exception as exc:  # noqa: BLE001
        click.echo(f"  ⚠  Could not read registry: {exc}")
        return

    if agents:
        click.echo(f"\n  Found {len(agents)} registered agent(s):")
        for agent in agents:
            status = "active" if agent.active else "inactive"
            click.echo(f"    •  {agent.agent_id}  ({agent.runtime_type}, {status})")
        return

    click.echo("\n  No agents are registered yet.")
    if not click.confirm("  Register a first agent now?", default=True):
        click.echo("  ⚠  No agents registered.\n     Use  orchestrator register-agent  when ready.")
        return

    _interactive_register_agent(registry)


def _interactive_register_agent(registry: Any) -> None:
    """Collect agent details interactively and write to the registry."""
    from src.core.models import AgentProps  # noqa: PLC0415

    click.echo()
    agent_id: str = click.prompt("  Agent ID", default="agent-worker-001")
    name: str = click.prompt("  Agent display name", default="Default Worker")
    runtime_type: str = click.prompt(
        "  Runtime type",
        type=click.Choice(["gemini", "claude", "pi", "dry-run"]),
        default="gemini",
    )
    capabilities_raw: str = click.prompt(
        "  Capabilities (comma-separated)",
        default="code:backend",
    )

    agent = AgentProps(
        agent_id=agent_id,
        name=name,
        capabilities=[c.strip() for c in capabilities_raw.split(",") if c.strip()],
        runtime_type=runtime_type,
        active=True,
    )
    registry.register(agent)
    click.echo(f"\n  ✓ Agent '{agent_id}' registered  ({runtime_type})")
