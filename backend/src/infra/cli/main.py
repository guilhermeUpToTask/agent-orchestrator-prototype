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
