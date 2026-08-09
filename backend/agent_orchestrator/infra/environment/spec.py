"""Where a project's environment spec is stored.

The same door the forge binding uses (`infra/forge/binding.py`) and for the same
reason: the config store is already two-tier with a project id as a scope, so
none of this touches the frozen `ProjectDefinition` and no un-freeze is needed.

**The operator authors these values, never a model.** An LLM-authored boot shell
run against a live application is the failure mode the whole design avoids; a
reasoner may later propose *what to check*, from the cycle's own approved
intent, but never how to start the thing.

This module is the ONE place the key names live.
"""

from __future__ import annotations

from agent_orchestrator.app.environment_port import EnvironmentSpec
from agent_orchestrator.infra.db.reference_repos import SqliteConfigStore

# Orchestrator-scoped, not project-scoped: which container CLI exists is a
# property of the machine, not of the project being built.
CONTAINER_BINARY_KEY = "environment.container_binary"

_DEFAULT_CONTAINER_BINARY = "docker"

IMAGE_KEY = "environment.image"
COMMAND_KEY = "environment.command"
PORT_KEY = "environment.port"
HEALTHCHECK_KEY = "environment.healthcheck"
SCENARIO_KEY = "environment.scenario"  # newline-separated commands
STARTUP_TIMEOUT_KEY = "environment.startup_timeout_seconds"

_DEFAULT_STARTUP_TIMEOUT = 120


def _int_or(value: str | None, fallback: int) -> int:
    """A malformed stored value degrades to the default rather than raising.

    An acceptance run is advisory; a typo in one config key must not be able to
    take down the promotion or the publication gate it was only observing.
    """
    if value is None or not value.strip():
        return fallback
    try:
        parsed = int(value)
    except ValueError:
        return fallback
    return parsed if parsed > 0 else fallback


def read_container_binary(config_store: SqliteConfigStore) -> str:
    """The container CLI to shell out to.

    Configuration rather than a hardcoded `docker`: podman, colima and rancher
    are CLI-compatible for everything the adapter uses, and stranding those
    operators buys nothing. Orchestrator-scoped — see `CONTAINER_BINARY_KEY`.
    """
    configured = config_store.get("orchestrator", CONTAINER_BINARY_KEY)
    if not configured or not configured.strip():
        return _DEFAULT_CONTAINER_BINARY
    return configured.strip()


def read_environment_spec(
    config_store: SqliteConfigStore, project_id: str
) -> EnvironmentSpec | None:
    """`None` when no image is configured — the default, supported state."""
    image = config_store.get(project_id, IMAGE_KEY)
    if not image or not image.strip():
        return None
    port = config_store.get(project_id, PORT_KEY)
    scenario = config_store.get(project_id, SCENARIO_KEY) or ""
    return EnvironmentSpec(
        image=image.strip(),
        command=(config_store.get(project_id, COMMAND_KEY) or None),
        port=_int_or(port, 0) or None,
        healthcheck=(config_store.get(project_id, HEALTHCHECK_KEY) or None),
        scenario=[line for line in scenario.splitlines() if line.strip()],
        startup_timeout_seconds=_int_or(
            config_store.get(project_id, STARTUP_TIMEOUT_KEY), _DEFAULT_STARTUP_TIMEOUT
        ),
    )
