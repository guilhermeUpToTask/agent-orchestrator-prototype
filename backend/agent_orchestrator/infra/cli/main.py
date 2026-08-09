"""
agent_orchestrator/infra/cli/main.py — the orchestrate CLI (fundamental commands only,
roadmap 4.2).

    orchestrate version [--json]            what this install is, for a report
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

from agent_orchestrator.infra.cli.error_handler import catch_domain_errors, ok


@click.group()
def cli() -> None:
    """AIPOM agent orchestrator."""


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------


def _package_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("agent-orchestrator")
    except PackageNotFoundError:
        # Running from a source tree that was never installed. Not an error —
        # the commit below is the better identifier for that case anyway.
        return "unknown"


def _commit() -> str:
    """The checkout's SHA, or `unknown` for an installed copy with no repository.

    Deliberately probes the directory the PACKAGE was imported from rather than
    the working directory: `orchestrate version` run from inside some other
    git repository must not report that repository's SHA as the orchestrator's.
    """
    import subprocess
    from pathlib import Path

    package_root = Path(__file__).resolve().parents[3]
    try:
        result = subprocess.run(
            ["git", "-C", str(package_root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() if result.returncode == 0 else "unknown"


@cli.command("version")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def version_cmd(as_json: bool) -> None:
    """Print what this install is, for a run report.

    Two runs can only be compared if both say which orchestrator produced them,
    and until this existed the guides had to tell people to run `git rev-parse`
    by hand — which an installed copy cannot do at all.
    """
    import platform
    import sys

    facts = {
        "version": _package_version(),
        "commit": _commit(),
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.machine()}",
        "executable": sys.executable,
    }

    if as_json:
        click.echo(json.dumps(facts, indent=2))
        return
    click.echo(f"agent-orchestrator {facts['version']}")
    for key in ("commit", "python", "platform"):
        click.echo(f"  {key:<9} {facts[key]}")


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
    from alembic import command

    from agent_orchestrator.infra.container import AppContainer
    from agent_orchestrator.infra.db.engine import db_url_for_home
    from agent_orchestrator.infra.db.migration_config import alembic_config

    container = AppContainer.from_env()
    # Resolved from the PACKAGE, not the repository: an installed copy has no
    # repository, and the old repo-relative lookup handed alembic its own
    # library directory (see migration_config.py).
    cfg = alembic_config(db_url_for_home(container.orchestrator_home))
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

    from agent_orchestrator.api.server import create_app

    # No uvicorn access log: RequestLoggingMiddleware already records every
    # request structurally (path only — never the query string), and /api/events
    # carries the API token in its query string because EventSource cannot send
    # headers. See agent_orchestrator/api/security.py::require_api_token_or_query.
    uvicorn.run(create_app(), host=host, port=port, access_log=False)


@cli.command("serve")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8000, show_default=True)
@click.option("--worker-id", default="worker-1", show_default=True)
@click.option("--poll-seconds", default=1.0, show_default=True)
@click.option("--lease-seconds", default=300, show_default=True)
@click.option("--max-concurrent-goals", default=4, show_default=True)
@click.option("--no-worker", is_flag=True, help="API only; drive the worker yourself.")
@click.option("--no-migrate", is_flag=True, help="Refuse to start if the schema is behind.")
@catch_domain_errors
def serve(
    host: str,
    port: int,
    worker_id: str,
    poll_seconds: float,
    lease_seconds: int,
    max_concurrent_goals: int,
    no_worker: bool,
    no_migrate: bool,
) -> None:
    """Migrate, then run the API (with the UI) and a worker together.

    The one command a new install needs. Doing it in three — `db upgrade`,
    `api start`, `worker start` — meant three shells sharing an environment,
    and forgetting the third produced the hardest failure to diagnose there is:
    a plan that is accepted and then never moves while every read still reports
    healthy.

    Migrating here is deliberate. The state directory belongs to the operator
    running this command, the migration is idempotent, and a first run that
    stops to tell you about a schema you have never seen is a worse
    introduction than one that just prepares it. `--no-migrate` opts out for
    anyone who wants the schema pinned.

    The worker stays a SEPARATE PROCESS rather than a task inside the API. It
    is the architecture (`docs/architecture/`), and it is also what lets a
    crashed or wedged worker be seen and restarted without taking the control
    plane down with it.
    """
    import signal
    import subprocess
    import sys

    import uvicorn

    from agent_orchestrator.api.server import create_app
    from agent_orchestrator.infra.container import AppContainer

    container = AppContainer.from_env()
    if not no_migrate:
        from alembic import command as alembic_command

        from agent_orchestrator.infra.db.engine import db_url_for_home
        from agent_orchestrator.infra.db.migration_config import alembic_config

        alembic_command.upgrade(
            alembic_config(db_url_for_home(container.orchestrator_home)), "head"
        )
    ok(f"state directory {container.orchestrator_home}")

    child: subprocess.Popen[bytes] | None = None
    if not no_worker:
        child = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "agent_orchestrator.infra.cli.main",
                "worker",
                "start",
                "--worker-id",
                worker_id,
                "--poll-seconds",
                str(poll_seconds),
                "--lease-seconds",
                str(lease_seconds),
                "--max-concurrent-goals",
                str(max_concurrent_goals),
            ]
        )
        ok(f"worker {worker_id} started (pid {child.pid})")

    def stop_worker() -> None:
        if child is None or child.poll() is not None:
            return
        child.terminate()
        try:
            child.wait(timeout=30)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=10)

    def stop_worker_then_die(signum: int, _frame: object) -> None:
        # uvicorn captures SIGINT/SIGTERM itself: it drains, restores whatever
        # handler was installed before it ran, and then RE-RAISES the signal.
        # So this runs after the API is fully down -- and it is the only place
        # the worker can still be reaped, because that re-raise kills this
        # process from inside `uvicorn.run()` and the `finally` below never
        # executes. Without it, `kill <pid>` (systemd, a supervisor, any
        # process manager) stops the API and leaves the worker running against
        # the same state directory, where the next `serve` puts a second one
        # beside it. Ctrl-C hides the bug: the terminal signals the whole
        # process group, so the worker gets its own SIGINT.
        stop_worker()
        signal.signal(signum, signal.SIG_DFL)
        signal.raise_signal(signum)

    for stop_signal in (signal.SIGINT, signal.SIGTERM):
        signal.signal(stop_signal, stop_worker_then_die)

    ok(f"http://{host}:{port}")
    try:
        # access_log off for the same reason as `api start`: the structured
        # middleware already records every request, path only.
        uvicorn.run(create_app(), host=host, port=port, access_log=False)
    finally:
        # Reached when uvicorn returns without a signal (a bind failure, an
        # unhandled startup error). The signal path above cannot get here.
        stop_worker()


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
    help="Plan lease duration; active actions renew it every one-third interval.",
)
@click.option(
    "--max-concurrent-goals",
    default=4,
    show_default=True,
    help=(
        "This process's own in-process goal-worker pool cap (domain unfreeze #14) -- "
        "how many independent, ready goals this single `worker start` process drives "
        "concurrently. Not derived from load testing yet; tune empirically."
    ),
)
@catch_domain_errors
def worker_start(
    worker_id: str, poll_seconds: float, lease_seconds: int, max_concurrent_goals: int
) -> None:
    """Run the claim-and-drive loop (config key agent_runner.mode selects
    dry-run or real; each agent's runtime_type picks its CLI runtime)."""
    from agent_orchestrator.infra.container import AppContainer
    from agent_orchestrator.infra.worker.main import run_worker_forever

    container = AppContainer.from_env()
    try:
        asyncio.run(
            run_worker_forever(
                container,
                worker_id=worker_id,
                poll_seconds=poll_seconds,
                lease_seconds=lease_seconds,
                max_concurrent_goals=max_concurrent_goals,
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
    from agent_orchestrator.infra.container import AppContainer

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
    from agent_orchestrator.infra.container import AppContainer

    AppContainer.from_env().config_store.set(scope, key, value)
    ok(f"{scope}/{key} = {value}")


@config.command("list")
@click.option("--scope", default="orchestrator", show_default=True)
@catch_domain_errors
def config_list(scope: str) -> None:
    from agent_orchestrator.infra.container import AppContainer

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

    from agent_orchestrator.domain.entities.capability import Capability
    from agent_orchestrator.domain.entities.agent_spec import AgentSpec
    from agent_orchestrator.domain.entities.ia_model import IAModel
    from agent_orchestrator.domain.entities.model_provider import ModelProvider
    from agent_orchestrator.domain.errors.config_errors import EntityAlreadyExistsError
    from agent_orchestrator.domain.policies.retry_policies import RetryPolicy
    from agent_orchestrator.infra.container import AppContainer
    from agent_orchestrator.infra.db.secret_ref import SecretRef

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
        Capability(
            id="test_authoring",
            name="Test authoring",
            description="authors authoritative tests before implementation",
        ),
        Capability(
            id="implementation",
            name="Implementation",
            description="implements changes against frozen tests",
        ),
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
            instructions=(
                "Implement the task exactly as described. Do NOT edit the "
                "frozen tests — they are the specification you must satisfy."
            ),
            # Deliberately WITHOUT test_authoring: a TDD task's two stages are
            # two different jobs, and the registry has to be able to say so.
            # A single agent holding every capability is what forced role
            # resolution to be permissive, which is how the implementer stage
            # ended up bound to a test author (P8.4, 2026-08-09).
            capabilities=[c for c in capabilities if c.id != "test_authoring"],
            default_retry=RetryPolicy(),
            # seeded valid out of the box; the LLM branch below re-binds it
            runtime_type="dry-run",
        ),
    )
    upsert(
        container.agent_repo,
        AgentSpec(
            id="test-agent",
            name="test-agent",
            role="test_author",
            model_role="smart",
            instructions=(
                "You are a TEST AUTHOR working test-first (TDD). Do NOT "
                "implement the feature. Author executable tests that specify "
                "the task's acceptance criteria and FAIL against the current "
                "code, and give the exact command that runs them."
            ),
            capabilities=[c for c in capabilities if c.id != "implementation"],
            default_retry=RetryPolicy(),
            runtime_type="dry-run",
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

    # Bind the demo agent's runtime to the seeded provider/model: pi when the
    # provider maps to a pi backend, otherwise stay on the dry-run dummy.
    from agent_orchestrator.infra.runtime.cli_runner import PI_BACKEND_ENV_VAR

    runtime_type = "pi" if provider_name in PI_BACKEND_ENV_VAR else "dry-run"
    agent = container.agent_repo.get("dev-agent")
    container.agent_repo.update(
        agent.model_copy(
            update={
                "runtime_type": runtime_type,
                "provider_id": provider_name,
                "model_id": model_id,
            }
        )
    )
    ok(
        f"seeded capabilities + dev-agent (runtime={runtime_type}); "
        f"reasoner.mode = llm (provider={provider_name}, model={model_name})"
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
    from agent_orchestrator.infra.container import AppContainer

    uow = AppContainer.from_env().new_unit_of_work()
    summaries = uow.plans.list_summaries()
    if not summaries:
        click.echo("(no plans)")
        return
    for s in summaries:
        claimed = f" [claimed by {s['claimed_by']}]" if s["claimed_by"] else ""
        click.echo(f"{s['id']}  {s['phase']:<16} iter={s['iteration']} v{s['version']}{claimed}")


@plan.command("show")
@click.argument("plan_id")
@catch_domain_errors
def plan_show(plan_id: str) -> None:
    from agent_orchestrator.infra.container import AppContainer

    uow = AppContainer.from_env().new_unit_of_work()
    with uow:
        found = uow.plans.get(plan_id)
    click.echo(json.dumps(found.model_dump(mode="json"), indent=2))


if __name__ == "__main__":
    cli()
