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

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.api.dependencies import set_container
from src.api.exceptions import register_exception_handlers
from src.api.routers import agents, discovery, events, goals, plan, project, refinement, spec, tasks
from src.api.schemas.common import HealthResponse
from src.api.sse import publish_sse

log = structlog.get_logger(__name__)

_API_VERSION = "0.2.0"


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
    leave it as ``None`` in production and it will be resolved from env.
    """
    if container is None:
        from src.infra.container import AppContainer
        container = AppContainer.from_env()

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

    # ── Dependency injection ──────────────────────────────────────────────────
    set_container(container)

    # ── SSE hook — planner events → event stream ──────────────────────────────
    def _planner_hook(event_type: str, data: dict) -> None:
        publish_sse(f"plan.{event_type}", data)

    try:
        container.planner_orchestrator.set_planner_event_hook(_planner_hook)
    except Exception as exc:
        log.warning("api.planner_hook_setup_failed", error=str(exc))

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
