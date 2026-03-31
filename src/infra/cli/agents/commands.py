"""
src/infra/cli/agents/commands.py — Agent management commands.

Commands:
  orchestrator agents create  — register a new agent
  orchestrator agents list    — list all registered agents
  orchestrator agents delete  — deregister an agent
  orchestrator agents edit    — update agent fields
"""

from __future__ import annotations

import json

import click

from src.infra.cli.error_handler import catch_domain_errors, die, ok


@click.group("agents")
def agents_group():
    """Manage agents."""


@agents_group.command("create")
@click.option("--agent-id", required=True)
@click.option("--name", required=True)
@click.option("--capabilities", required=True, help="Comma-separated, e.g. code:backend")
@click.option("--version", default="1.0.0")
@click.option("--active/--inactive", default=True, help="Include in scheduling")
@click.option("--runtime-type", default="gemini", help="gemini | claude | pi | dry-run")
@click.option(
    "--runtime-config",
    default="{}",
    help='JSON string, e.g. \'{"model":"gemini-2.0-pro"}\'',
)
@catch_domain_errors
def agent_create(
    agent_id: str,
    name: str,
    capabilities: str,
    version: str,
    active: bool,
    runtime_type: str,
    runtime_config: str,
):
    """Register a new agent in the registry."""
    from src.domain import AgentProps
    from src.infra.container import AppContainer

    try:
        config = json.loads(runtime_config)
    except json.JSONDecodeError as exc:
        die(f"Invalid --runtime-config JSON: {exc}")

    agent = AgentProps(
        agent_id=agent_id,
        name=name,
        capabilities=[c.strip() for c in capabilities.split(",") if c.strip()],
        version=version,
        active=active,
        runtime_type=runtime_type,
        runtime_config=config,
    )
    result = AppContainer.from_env().agent_register_usecase.execute(agent)
    status = "active" if result.active else "inactive"
    ok(f"Agent registered: {result.agent_id}  ({status}, runtime: {result.runtime_type})")


@agents_group.command("list")
@catch_domain_errors
def agent_list():
    """List all registered agents."""
    from src.infra.container import AppContainer

    agents = AppContainer.from_env().agent_registry.list_agents()

    if not agents:
        click.echo("No agents registered.")
        return

    click.echo(f"\n{'AGENT ID':<25} {'STATUS':<10} {'RUNTIME':<12} {'VERSION':<10} CAPABILITIES")
    click.echo("-" * 80)
    for a in agents:
        status = "active" if a.active else "inactive"
        caps = ", ".join(a.capabilities) or "-"
        click.echo(f"{a.agent_id:<25} {status:<10} {a.runtime_type:<12} {a.version:<10} {caps}")


@agents_group.command("delete")
@click.argument("agent_id")
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip confirmation prompt")
@catch_domain_errors
def agent_delete(agent_id: str, yes: bool):
    """Deregister an agent from the registry."""
    from src.infra.container import AppContainer

    registry = AppContainer.from_env().agent_registry
    agent = registry.get(agent_id)
    if agent is None:
        die(f"Agent not found: {agent_id}")
        return

    if not yes:
        click.confirm(
            f"  Deregister agent {agent_id} ({agent.runtime_type})?",
            abort=True,
        )

    registry.deregister(agent_id)
    ok(f"Agent {agent_id} deregistered")


@agents_group.command("edit")
@click.argument("agent_id")
@click.option("--name", default=None, help="New display name")
@click.option("--capabilities", default=None, help="New capabilities (comma-separated)")
@click.option("--version", default=None, help="New version string")
@click.option("--active/--inactive", default=None, help="Enable or disable the agent")
@click.option("--runtime-type", default=None, help="New runtime type")
@click.option("--runtime-config", default=None, help="New runtime config JSON")
@catch_domain_errors
def agent_edit(
    agent_id: str,
    name: str | None,
    capabilities: str | None,
    version: str | None,
    active: bool | None,
    runtime_type: str | None,
    runtime_config: str | None,
):
    """Update one or more fields of an existing agent."""
    from src.infra.container import AppContainer

    registry = AppContainer.from_env().agent_registry
    agent = registry.get(agent_id)
    if agent is None:
        die(f"Agent not found: {agent_id}")
        return

    # Build an updated copy by merging provided fields
    data = agent.model_dump()
    if name is not None:
        data["name"] = name
    if version is not None:
        data["version"] = version
    if runtime_type is not None:
        data["runtime_type"] = runtime_type
    if active is not None:
        data["active"] = active
    if capabilities is not None:
        data["capabilities"] = [c.strip() for c in capabilities.split(",") if c.strip()]
    if runtime_config is not None:
        try:
            data["runtime_config"] = json.loads(runtime_config)
        except json.JSONDecodeError as exc:
            die(f"Invalid --runtime-config JSON: {exc}")

    from src.domain import AgentProps

    updated = AgentProps(**data)
    registry.register(updated)
    ok(f"Agent {agent_id} updated")
