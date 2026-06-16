"""
src/infra/cli/capabilities/commands.py — Capability tag management.

Commands:
  orchestrate capabilities list    — list all registered capability tags
  orchestrate capabilities add     — register a new tag
  orchestrate capabilities remove  — remove a tag
"""

from __future__ import annotations

import click

from src.infra.cli.error_handler import catch_domain_errors, die, ok


@click.group("capabilities")
def capabilities_group():
    """Manage capability tags (the shared agent/task vocabulary)."""


@capabilities_group.command("list")
@catch_domain_errors
def capabilities_list():
    """List all registered capability tags."""
    from src.infra.container import AppContainer

    tags = AppContainer.from_env().capability_registry.list_tags()
    if not tags:
        click.echo("No capability tags registered.")
        return
    click.echo("\nCAPABILITY TAGS")
    click.echo("-" * 30)
    for tag in tags:
        click.echo(f"  {tag}")


@capabilities_group.command("add")
@click.argument("tag")
@catch_domain_errors
def capabilities_add(tag: str):
    """Register a new capability tag (e.g. code:backend, test:write)."""
    from src.infra.container import AppContainer

    registry = AppContainer.from_env().capability_registry
    try:
        registry.add(tag)
    except ValueError as exc:
        die(str(exc))
    ok(f"Capability registered: {tag}")


@capabilities_group.command("remove")
@click.argument("tag")
@catch_domain_errors
def capabilities_remove(tag: str):
    """Remove a capability tag."""
    from src.infra.container import AppContainer

    AppContainer.from_env().capability_registry.remove(tag)
    ok(f"Capability removed: {tag}")
