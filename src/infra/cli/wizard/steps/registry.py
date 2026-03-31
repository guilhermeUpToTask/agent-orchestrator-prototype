"""
src/infra/cli/wizard/steps/registry.py — Wizard Step 3: agent registry setup.
"""

from __future__ import annotations

from typing import Any, Callable

import click


def setup_registry(
    config_data: dict[str, Any],
    registry_factory: Callable | None = None,
) -> None:
    """
    Check the agent registry. If it is empty, offer an interactive prompt to
    register a first agent so `orchestrator start` can succeed immediately.
    """
    if registry_factory is None:
        from src.infra.container import AppContainer

        def _factory():
            return AppContainer.from_env(project_name=config_data["project_name"]).agent_registry

        registry_factory = _factory

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
        click.echo("  ⚠  No agents registered.\n     Use  orchestrator agents create  when ready.")
        return

    _interactive_register_agent(registry)


def _interactive_register_agent(registry: Any) -> None:
    """Collect agent details interactively and write to the registry."""
    from src.domain import AgentProps  # noqa: PLC0415

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
