"""
src/infra/cli/main.py — the orchestrate CLI (fundamental commands only,
roadmap 4.2).

    orchestrate db upgrade                  run migrations to head
    orchestrate api start [--port]          serve the FastAPI app
    orchestrate worker start [--worker-id]  run the claim-and-drive worker
    orchestrate config get|set|list         two-tier config (SQLite)
    orchestrate plan list|show              read-only plan inspection

The old command accretion (task/goal/spec/wizard groups) went with the
pre-refactor architecture: mutations go through the API; the worker and the
API are the only long-running processes.
"""

from __future__ import annotations

import asyncio
import json

import click

from src.infra.cli.error_handler import catch_domain_errors, ok


@click.group()
def cli() -> None:
    """AIPOM agent orchestrator."""


# ---------------------------------------------------------------------------
# db
# ---------------------------------------------------------------------------

@cli.group()
def db() -> None:
    """Database maintenance."""


@db.command("upgrade")
@catch_domain_errors
def db_upgrade() -> None:
    """Apply migrations up to head for the ORCHESTRATOR_HOME database."""
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    from src.infra.container import AppContainer
    from src.infra.db.engine import db_url_for_home

    container = AppContainer.from_env()
    backend_root = Path(__file__).resolve().parents[3]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    cfg.set_main_option(
        "sqlalchemy.url", db_url_for_home(container.orchestrator_home)
    )
    command.upgrade(cfg, "head")
    ok(f"database migrated to head under {container.orchestrator_home}")


# ---------------------------------------------------------------------------
# api / worker
# ---------------------------------------------------------------------------

@cli.group()
def api() -> None:
    """The FastAPI server."""


@api.command("start")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8000, show_default=True)
@catch_domain_errors
def api_start(host: str, port: int) -> None:
    """Serve the orchestrator API (runs the outbox->SSE relay in-process)."""
    import uvicorn

    from src.api.server import create_app

    uvicorn.run(create_app(), host=host, port=port)


@cli.group()
def worker() -> None:
    """The plan-driving worker."""


@worker.command("start")
@click.option("--worker-id", default="worker-1", show_default=True)
@click.option("--poll-seconds", default=1.0, show_default=True)
@click.option(
    "--lease-seconds",
    default=300,
    show_default=True,
    help="Must exceed the longest expected single task run "
    "(heartbeats happen between units).",
)
@catch_domain_errors
def worker_start(worker_id: str, poll_seconds: float, lease_seconds: int) -> None:
    """Run the claim-and-drive loop (AGENT_MODE selects the runtime)."""
    from src.infra.container import AppContainer
    from src.infra.worker.main import run_worker_forever

    container = AppContainer.from_env()
    try:
        asyncio.run(
            run_worker_forever(
                container,
                worker_id=worker_id,
                poll_seconds=poll_seconds,
                lease_seconds=lease_seconds,
            )
        )
    except KeyboardInterrupt:
        ok(f"worker {worker_id} stopped")


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

@cli.group()
def config() -> None:
    """Two-tier config (scope 'orchestrator' or a project id)."""


@config.command("get")
@click.argument("key")
@click.option("--scope", default="orchestrator", show_default=True)
@catch_domain_errors
def config_get(key: str, scope: str) -> None:
    from src.infra.container import AppContainer

    value = AppContainer.from_env().config_store.get(scope, key)
    if value is None:
        raise KeyError(f"{scope}/{key} is not set")
    click.echo(value)


@config.command("set")
@click.argument("key")
@click.argument("value")
@click.option("--scope", default="orchestrator", show_default=True)
@catch_domain_errors
def config_set(key: str, value: str, scope: str) -> None:
    from src.infra.container import AppContainer

    AppContainer.from_env().config_store.set(scope, key, value)
    ok(f"{scope}/{key} = {value}")


@config.command("list")
@click.option("--scope", default="orchestrator", show_default=True)
@catch_domain_errors
def config_list(scope: str) -> None:
    from src.infra.container import AppContainer

    for key, value in sorted(AppContainer.from_env().config_store.all(scope).items()):
        click.echo(f"{key} = {value}")


# ---------------------------------------------------------------------------
# seed — idempotent demo/bootstrap data
# ---------------------------------------------------------------------------

# Each provider is just an OpenAI-compatible endpoint + which env var holds
# its key by convention (ported from the old planner factory presets).
# `local` has no default endpoint — it requires an explicit --base-url.
_PROVIDER_PRESETS: dict[str, tuple[str, str]] = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "anthropic": ("https://api.anthropic.com/v1/", "ANTHROPIC_API_KEY"),
    "gemini": (
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "GEMINI_API_KEY",
    ),
    "local": ("", "OPENAI_API_KEY"),
}


@cli.group()
def seed() -> None:
    """Idempotent bootstrap data (capabilities, default agent, reasoner)."""


@seed.command("demo")
@click.option(
    "--provider",
    "provider_name",
    type=click.Choice(sorted(_PROVIDER_PRESETS)),
    default=None,
    help="LLM provider preset for the reasoner (omit with --stub).",
)
@click.option("--model", "model_name", default=None, help="Provider model string.")
@click.option("--base-url", default=None, help="Override the preset base_url.")
@click.option(
    "--api-key-env",
    default=None,
    help="Env var holding the provider key (read ONCE here, stored encrypted).",
)
@click.option(
    "--stub",
    is_flag=True,
    help="Deterministic stub reasoner: no provider, no key, no master key.",
)
@catch_domain_errors
def seed_demo(
    provider_name: str | None,
    model_name: str | None,
    base_url: str | None,
    api_key_env: str | None,
    stub: bool,
) -> None:
    """Seed capabilities + a default agent, and configure the reasoner.

    Stub mode:  orchestrate seed demo --stub
    LLM mode:   orchestrate seed demo --provider openrouter --model <name> \\
                    [--api-key-env OPENROUTER_API_KEY] [--base-url URL]
    """
    import os

    from src.domain.entities.capability import Capability
    from src.domain.entities.agent_spec import AgentSpec
    from src.domain.entities.ia_model import IAModel
    from src.domain.entities.model_provider import ModelProvider
    from src.domain.errors.config_errors import EntityAlreadyExistsError
    from src.domain.policies.retry_policies import RetryPolicy
    from src.infra.container import AppContainer
    from src.infra.db.secret_ref import SecretRef

    container = AppContainer.from_env()

    def upsert(repo, entity) -> None:
        try:
            repo.add(entity)
        except EntityAlreadyExistsError:
            repo.update(entity)

    capabilities = [
        Capability(id="backend", name="Backend", description="server-side code"),
        Capability(id="frontend", name="Frontend", description="UI code"),
        Capability(id="testing", name="Testing", description="tests and QA"),
    ]
    for cap in capabilities:
        upsert(container.capability_repo, cap)

    upsert(
        container.agent_repo,
        AgentSpec(
            id="dev-agent",
            name="dev-agent",
            role="implementer",
            model_role="smart",
            instructions="Implement the task exactly as described.",
            capabilities=capabilities,
            default_retry=RetryPolicy(),
        ),
    )
    container.agent_repo.set_default("dev-agent")

    config = container.config_store
    scope = config.ORCHESTRATOR_SCOPE

    if stub:
        config.set(scope, "reasoner.mode", "stub")
        ok("seeded capabilities + dev-agent; reasoner.mode = stub")
        return

    if not provider_name or not model_name:
        raise click.UsageError("--provider and --model are required without --stub")
    preset_url, preset_env = _PROVIDER_PRESETS[provider_name]
    resolved_url = base_url or preset_url
    if not resolved_url:
        raise click.UsageError(f"provider '{provider_name}' requires --base-url")

    key_env = api_key_env or preset_env
    api_key = os.environ.get(key_env, "").strip()
    if not api_key:
        raise click.UsageError(
            f"environment variable {key_env} is empty — export the provider "
            "key there (it is read once and stored envelope-encrypted)."
        )

    key_ref = SecretRef.for_provider(provider_name)
    container.secret_store.put(key_ref, api_key)

    model_id = f"{provider_name}:{model_name}"
    upsert(
        container.provider_repo,
        ModelProvider(
            id=provider_name,
            name=provider_name,
            base_url=resolved_url,
            api_key_ref=key_ref.uri,
            models=[IAModel(id=model_id, provider_id=provider_name, name=model_name)],
        ),
    )

    config.set(scope, "reasoner.mode", "llm")
    config.set(scope, "reasoner.provider_id", provider_name)
    config.set(scope, "reasoner.model_id", model_id)
    ok(
        f"seeded capabilities + dev-agent; reasoner.mode = llm "
        f"(provider={provider_name}, model={model_name})"
    )


# ---------------------------------------------------------------------------
# plan (read-only inspection; mutations go through the API)
# ---------------------------------------------------------------------------

@cli.group()
def plan() -> None:
    """Read-only plan inspection."""


@plan.command("list")
@catch_domain_errors
def plan_list() -> None:
    from src.infra.container import AppContainer

    uow = AppContainer.from_env().new_unit_of_work()
    summaries = uow.plans.list_summaries()
    if not summaries:
        click.echo("(no plans)")
        return
    for s in summaries:
        claimed = f" [claimed by {s['claimed_by']}]" if s["claimed_by"] else ""
        click.echo(
            f"{s['id']}  {s['phase']:<16} iter={s['iteration']} "
            f"v{s['version']}{claimed}"
        )


@plan.command("show")
@click.argument("plan_id")
@catch_domain_errors
def plan_show(plan_id: str) -> None:
    from src.infra.container import AppContainer

    uow = AppContainer.from_env().new_unit_of_work()
    with uow:
        found = uow.plans.get(plan_id)
    click.echo(json.dumps(found.model_dump(mode="json"), indent=2))


if __name__ == "__main__":
    cli()
