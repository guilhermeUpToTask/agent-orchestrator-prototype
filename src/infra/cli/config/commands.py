"""
src/infra/cli/config/commands.py — SQLite control-plane commands.

Drives the global config store (projects, providers, models, agent definitions)
and the encrypted secret store. These are siblings of the API: both are thin
layers over ProjectService / RegistryService. Secrets are prompted with hidden
input and never echoed back.

Requires ORCHESTRATOR_MASTER_KEY in the environment for any command that touches
secrets (create-project --github-token, register-provider, set-secret, import).
"""
from __future__ import annotations

import json

import click

from src.infra.cli.error_handler import catch_domain_errors, info, ok
from src.infra.db.active_project import CLI_SESSION
from src.domain.value_objects.config import ProviderKind, SecretRef


@click.group("config")
def config_group():
    """Manage global config: projects, providers, models, agents, secrets (SQLite)."""


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

@config_group.command("create-project")
@click.option("--name", required=True, help="Human-readable project name.")
@click.option("--repo-url", required=True, help="Git repository URL.")
@click.option("--default-branch", default="main", show_default=True)
@click.option("--github-token", default=None, help="Optional; stored encrypted.")
@click.option("--id", "project_id", default=None, help="Override the derived id slug.")
@catch_domain_errors
def create_project(name, repo_url, default_branch, github_token, project_id):
    """Create a project in the config store."""
    from src.infra.container import AppContainer

    proj = AppContainer.from_env().project_service.create_project(
        name=name,
        repo_url=repo_url,
        default_branch=default_branch,
        github_token=github_token,
        project_id=project_id,
    )
    ok(f"Project created: {proj.id}")
    info(f"repo: {proj.repo_url}  branch: {proj.default_branch}")


@config_group.command("use-project")
@click.argument("project_id")
@catch_domain_errors
def use_project(project_id):
    """Set the active project (config-store session)."""
    from src.infra.container import AppContainer

    proj = AppContainer.from_env().project_service.activate(CLI_SESSION, project_id)
    ok(f"Active project: {proj.id}")


@config_group.command("list-projects")
@catch_domain_errors
def list_projects():
    """List all projects in the config store."""
    from src.infra.container import AppContainer

    app = AppContainer.from_env()
    active = app.active_project.get_active(CLI_SESSION)
    projects = app.project_service.list_projects()
    if not projects:
        info("No projects. Create one with: orchestrate config create-project")
        return
    for p in projects:
        marker = "*" if p.id == active else " "
        click.echo(f"  {marker} {p.id}  ({p.name})  {p.repo_url}")


# ---------------------------------------------------------------------------
# Providers + models
# ---------------------------------------------------------------------------

@config_group.command("register-provider")
@click.option("--id", "provider_id", required=True, help="Provider id (e.g. anthropic).")
@click.option(
    "--kind",
    required=True,
    type=click.Choice([k.value for k in ProviderKind]),
)
@click.option("--api-key", prompt=True, hide_input=True, help="Stored encrypted.")
@click.option("--base-url", default=None)
@click.option("--default-model", default=None)
@catch_domain_errors
def register_provider(provider_id, kind, api_key, base_url, default_model):
    """Register a model provider and store its API key (encrypted)."""
    from src.infra.container import AppContainer

    prov = AppContainer.from_env().registry_service.register_provider(
        provider_id=provider_id,
        kind=ProviderKind(kind),
        api_key=api_key,
        base_url=base_url,
        default_model=default_model,
    )
    ok(f"Provider registered: {prov.id} ({prov.kind.value})")


@config_group.command("add-model")
@click.option("--provider", "provider_id", required=True)
@click.option("--model-id", required=True)
@click.option("--display-name", default=None)
@catch_domain_errors
def add_model(provider_id, model_id, display_name):
    """Register a model on a provider."""
    from src.infra.container import AppContainer

    AppContainer.from_env().registry_service.add_model(
        provider_id=provider_id, model_id=model_id, display_name=display_name
    )
    ok(f"Model added: {model_id} -> {provider_id}")


@config_group.command("list-providers")
@catch_domain_errors
def list_providers():
    """List registered providers and their models."""
    from src.infra.container import AppContainer

    providers = AppContainer.from_env().registry_service.list_providers()
    if not providers:
        info("No providers registered.")
        return
    for p in providers:
        models = ", ".join(m.model_id for m in p.models) or "(no models)"
        click.echo(f"  {p.id}  [{p.kind.value}]  models: {models}")


# ---------------------------------------------------------------------------
# Agent definitions
# ---------------------------------------------------------------------------

@config_group.command("register-agent")
@click.option("--id", "agent_id", required=True)
@click.option("--name", required=True)
@click.option("--runtime-type", required=True, type=click.Choice(["pi", "claude", "gemini"]))
@click.option("--provider", "provider_id", required=True)
@click.option("--model", "model_id", required=True)
@click.option("--capability", "capabilities", multiple=True, help="Repeatable.")
@catch_domain_errors
def register_agent(agent_id, name, runtime_type, provider_id, model_id, capabilities):
    """Register a global agent definition."""
    from src.infra.container import AppContainer

    agent = AppContainer.from_env().registry_service.register_agent(
        agent_id=agent_id,
        name=name,
        runtime_type=runtime_type,
        provider_id=provider_id,
        model_id=model_id,
        capabilities=tuple(capabilities),
    )
    ok(f"Agent registered: {agent.id}")


@config_group.command("list-agents")
@catch_domain_errors
def list_agents():
    """List registered agent definitions."""
    from src.infra.container import AppContainer

    agents = AppContainer.from_env().registry_service.list_agents()
    if not agents:
        info("No agents registered.")
        return
    for a in agents:
        caps = ", ".join(a.capabilities) or "-"
        click.echo(f"  {a.id}  [{a.runtime_type}]  {a.provider_id}/{a.model_id}  caps: {caps}")


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------

@config_group.command("set-secret")
@click.option("--uri", required=True, help="e.g. secret://provider/anthropic")
@click.option("--value", prompt=True, hide_input=True)
@catch_domain_errors
def set_secret(uri, value):
    """Store an encrypted secret under a secret:// URI."""
    from src.infra.container import AppContainer

    AppContainer.from_env().secret_store.put(SecretRef(uri=uri), value)
    ok(f"Secret stored: {uri}")


# ---------------------------------------------------------------------------
# Import / export
# ---------------------------------------------------------------------------

@config_group.command("import")
@catch_domain_errors
def import_files():
    """Import legacy file-based config (.env + registry.json) into SQLite."""
    from src.infra.container import AppContainer
    from src.infra.db.importer import import_config

    app = AppContainer.from_env()
    report = import_config(
        orchestrator_home=app.ctx.machine.orchestrator_home,
        config_store=app.config_store,
        project_service=app.project_service,
        registry_service=app.registry_service,
    )
    ok("Import complete.")
    info(f"providers: {len(report.providers_created)}  "
         f"projects: {len(report.projects_created)}  "
         f"agents: {len(report.agents_created)}  "
         f"skipped: {len(report.skipped)}")


@config_group.command("import-tasks")
@catch_domain_errors
def import_tasks_cmd():
    """Import per-project tasks/*.yaml into the SQLite task store (Stage B)."""
    from src.infra.container import AppContainer
    from src.infra.db.importer import import_tasks
    from src.infra.db.task_store import SqliteTaskStore

    app = AppContainer.from_env()
    _, session_factory = app._config_db
    imported = import_tasks(
        orchestrator_home=app.ctx.machine.orchestrator_home,
        task_store=SqliteTaskStore(session_factory),
        config_store=app.config_store,
    )
    ok(f"Imported {len(imported)} task(s) into SQLite.")


@config_group.command("export")
@click.option("--format", "fmt", type=click.Choice(["yaml", "json"]), default="yaml")
@catch_domain_errors
def export(fmt):
    """Dump the config store (read-only; secrets masked) as YAML or JSON."""
    from src.infra.container import AppContainer
    from src.infra.db.exporter import export_config, export_config_yaml

    config = AppContainer.from_env().config_store
    if fmt == "json":
        click.echo(json.dumps(export_config(config), indent=2, default=str))
    else:
        click.echo(export_config_yaml(config))
