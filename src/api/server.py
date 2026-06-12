"""
src/api/server.py — AIPOM FastAPI application factory.

Responsibilities:
  1. Create the FastAPI application with OpenAPI metadata.
  2. Register global domain → HTTP exception handlers.
  3. Mount CORS middleware.
  4. Include all resource routers under /api.
  5. Bind the AppContainer to the dependency injection layer.
  6. Wire the planner SSE hook so plan events flow to the event stream.
  7. Expose the /health endpoint.

Zero business logic lives here.
"""
from __future__ import annotations

import asyncio
import os
import threading
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.dependencies import set_container, set_container_provider
from src.api.exceptions import register_exception_handlers
from src.api.routers import agents, discovery, events, goals, plan, project, refinement, spec, tasks
from src.api.schemas.common import HealthResponse
from src.api.sse import publish_sse

log = structlog.get_logger(__name__)

_API_VERSION = "0.2.0"

# Coordinator daemons (task manager, goal orchestrator, reconciler) run as
# lifespan threads inside this process — the single writer for task/goal
# state. Set ORCHESTRATOR_EMBED_COORDINATORS=0 to opt out (tests, or when
# running them as standalone CLI processes).
_COORDINATOR_STATE: dict[str, Any] = {}


def _coordinators_enabled() -> bool:
    return os.environ.get("ORCHESTRATOR_EMBED_COORDINATORS", "1") != "0"


def _start_coordinators(container: Any) -> None:
    """Resolve dependencies on this thread, then start the three loops.

    cached_property is not locked, so every coordinator dependency is
    touched here, on the single startup thread, before any thread runs.
    """
    from src.api.event_bridge import run_event_bridge
    from src.api.sse import get_broker
    from src.app.runners import (
        run_goal_orchestrator_loop,
        run_reconciler_loop,
        run_task_manager_loop,
    )

    interval = int(os.environ.get("RECONCILER_INTERVAL", "60"))
    stuck_age = int(os.environ.get("RECONCILER_STUCK_AGE", "120"))

    handler = container.task_manager_handler
    events_port = container.event_port
    orchestrator = container.task_graph_orchestrator
    reconciler = container.get_reconciler(
        interval_seconds=interval,
        stuck_task_min_age_seconds=stuck_age,
    )

    stop_event = threading.Event()
    threads = [
        threading.Thread(
            target=run_task_manager_loop,
            args=(handler, events_port, stop_event.is_set),
            daemon=True,
            name="task-manager",
        ),
        threading.Thread(
            target=run_goal_orchestrator_loop,
            args=(orchestrator,),
            daemon=True,
            name="goal-orchestrator",
        ),
        threading.Thread(
            target=run_reconciler_loop,
            args=(reconciler,),
            daemon=True,
            name="reconciler",
        ),
    ]
    # Redis → SSE bridge: makes worker/coordinator progress visible to the
    # frontend. Dry-run has no Redis; there, only router-originated events
    # reach the UI (the coordinators publish to the in-memory adapter).
    if container.ctx.machine.mode != "dry-run":
        threads.append(
            threading.Thread(
                target=run_event_bridge,
                args=(container._redis, get_broker(), stop_event.is_set),
                daemon=True,
                name="sse-event-bridge",
            )
        )
    for t in threads:
        t.start()

    _COORDINATOR_STATE.update(
        stop_event=stop_event,
        orchestrator=orchestrator,
        reconciler=reconciler,
        threads=threads,
    )
    log.info(
        "api.coordinators_started",
        reconciler_interval=interval,
        reconciler_stuck_age=stuck_age,
    )


def _stop_coordinators() -> None:
    if not _COORDINATOR_STATE:
        return
    _COORDINATOR_STATE["stop_event"].set()
    _COORDINATOR_STATE["orchestrator"].shutdown()
    _COORDINATOR_STATE["reconciler"].shutdown()
    for t in _COORDINATOR_STATE["threads"]:
        t.join(timeout=5.0)  # daemon threads — process exit is the backstop
    log.info("api.coordinators_stopped")
    _COORDINATOR_STATE.clear()


def _wire_planner_sse_hook(container: Any) -> None:
    """Forward planner events to the SSE stream; tolerate unconfigured projects."""

    def _planner_hook(event_type: str, data: dict[str, Any]) -> None:
        publish_sse(f"plan.{event_type}", data)

    try:
        container.planner_orchestrator.set_planner_event_hook(_planner_hook)
    except Exception as exc:
        log.warning("api.planner_hook_setup_failed", error=str(exc))


class DynamicContainerProvider:
    """
    Resolve the AppContainer lazily, rebuilding it whenever the active
    project context changes.

    The active project lives in ``.orchestrator/config.json`` and may be
    switched by the CLI while the API is running.  AppContainer caches all
    repositories and use cases per project, so a stale container would keep
    serving the previous project's spec, plan, and task state.  On every
    request we fingerprint the resolvable context (project_name + mode) and
    rebuild only when it changed — the common path is a tuple comparison.
    """

    def __init__(self) -> None:
        self._container: Any = None
        self._fingerprint: tuple[Any, ...] | None = None
        self._lock = threading.Lock()

    @staticmethod
    def _current_fingerprint() -> tuple[Any, ...]:
        from src.infra.settings import GlobalConfigStore

        stored = GlobalConfigStore().load()
        mode = os.environ.get("AGENT_MODE") or stored.get("mode")
        return (stored.get("project_name"), mode)

    def __call__(self) -> Any:
        from src.infra.container import AppContainer

        fingerprint = self._current_fingerprint()
        with self._lock:
            if self._container is None or fingerprint != self._fingerprint:
                log.info(
                    "api.container_rebuilt",
                    project_name=fingerprint[0],
                    mode=fingerprint[1],
                )
                if self._container is not None and _COORDINATOR_STATE:
                    log.warning(
                        "api.coordinators_bound_to_previous_project",
                        detail=(
                            "Embedded coordinators keep the container they "
                            "were started with — restart the API to point "
                            "them at the new project."
                        ),
                        project_name=fingerprint[0],
                    )
                self._container = AppContainer.from_env()
                self._fingerprint = fingerprint
                _wire_planner_sse_hook(self._container)
            return self._container


def _unique_operation_id(route) -> str:
    """
    Derive clean operation IDs for frontend client generators.

    Produces strings like ``plan-approve_brief`` so that tools such as
    ``openapi-ts`` or Orval generate typed methods like
    ``PlanClient.approveBrief()``.
    """
    tag = route.tags[0] if route.tags else "default"
    return f"{tag}-{route.name}"


def create_app(container=None) -> FastAPI:
    """
    Build and return the configured FastAPI application.

    Pass *container* explicitly in tests to inject a mock container;
    leave it as ``None`` in production and the active project context is
    re-resolved from ``.orchestrator/config.json`` on each request.

    In production (container is None) the lifespan also hosts the three
    coordinator daemons as threads, making this process the sole writer
    for task/goal state. Injected-container (test) apps skip them.
    """
    provider = DynamicContainerProvider() if container is None else None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        from src.api.sse import get_broker

        # Threadpool routers and the bridge thread publish from off-loop;
        # the broker needs the loop to hop onto it safely.
        get_broker().bind_loop(asyncio.get_running_loop())
        if provider is not None and _coordinators_enabled():
            try:
                _start_coordinators(provider())
            except Exception as exc:
                # An unconfigured project must not keep the API from serving
                # setup endpoints; coordinators need a restart once fixed.
                log.warning("api.coordinators_not_started", error=str(exc))
        yield
        _stop_coordinators()

    # ── App instance ──────────────────────────────────────────────────────────
    app = FastAPI(
        title="AIPOM Orchestrator API",
        version=_API_VERSION,
        description=(
            "RESTful API for the AIPOM AI project orchestrator. "
            "Exposes plan lifecycle, goal/task management, agent registry, "
            "spec validation, and a real-time SSE event stream."
        ),
        generate_unique_id_function=_unique_operation_id,
        lifespan=lifespan,
    )

    # ── Exception handlers ────────────────────────────────────────────────────
    register_exception_handlers(app)

    # ── CORS ──────────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Dependency injection + planner SSE hook ───────────────────────────────
    if container is not None:
        set_container(container)
        _wire_planner_sse_hook(container)
    else:
        set_container_provider(provider)

    # ── Routers ───────────────────────────────────────────────────────────────
    _prefix = "/api"

    app.include_router(plan.router,        prefix=_prefix)
    app.include_router(refinement.router,  prefix=_prefix)
    app.include_router(discovery.router,   prefix=_prefix)
    app.include_router(goals.router,       prefix=_prefix)
    app.include_router(tasks.router,       prefix=_prefix)
    app.include_router(agents.router,      prefix=_prefix)
    app.include_router(spec.router,        prefix=_prefix)
    app.include_router(project.router,     prefix=_prefix)
    app.include_router(events.router,      prefix=_prefix)

    # ── Health ────────────────────────────────────────────────────────────────
    @app.get(
        "/health",
        response_model=HealthResponse,
        tags=["system"],
        summary="Health Check",
        description="Returns `ok` when the API is up and the container is wired.",
    )
    def health() -> HealthResponse:
        return HealthResponse(status="ok", version=_API_VERSION)

    return app
