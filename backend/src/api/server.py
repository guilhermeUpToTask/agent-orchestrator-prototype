"""
src/api/server.py — FastAPI application factory (the thin API).

Responsibilities — zero business logic:
  1. Create the FastAPI app with OpenAPI metadata.
  2. Structured logging + the one error->HTTP mapping layer.
  3. CORS + request-correlation middleware.
  4. Mount the thin routers under /api (route -> use case).
  5. Lifespan: bind the SSE broker to the loop and run the OUTBOX RELAY thread
     (the thing that actually delivers outbox rows -> SSE; without it events
     are written but never seen).
  6. /health.

The worker runs as its own process (`orchestrate worker start`) — the old
in-process coordinator daemons are gone with the pre-refactor architecture.
"""

from __future__ import annotations

import asyncio
import os
import threading
from contextlib import asynccontextmanager
from typing import AsyncIterator

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute

from src.api.dependencies import get_container, set_container
from src.api.exceptions import register_exception_handlers
from src.api.logging.config import configure_logging
from src.api.middleware.request_logging import RequestLoggingMiddleware
from src.api.outbox_relay import run_outbox_relay
from src.api.routers import (
    config,
    events,
    metrics,
    plans,
    reasoner,
    reference,
    runner,
)
from src.api.schemas.common import HealthResponse
from src.api.sse import get_broker
from src.infra.container import AppContainer

log = structlog.get_logger(__name__)

_API_VERSION = "0.3.0"


def _cors_origins() -> list[str]:
    """Frontend origins allowed to read the API (incl. the SSE stream).
    Defaults cover the Vite dev server; override with CORS_ALLOW_ORIGINS."""
    raw = os.environ.get("CORS_ALLOW_ORIGINS")
    if raw:
        return [origin.strip() for origin in raw.split(",") if origin.strip()]
    return [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ]


def _unique_operation_id(route: APIRoute) -> str:
    """Clean operation IDs (`plans-create`) for typed client generators."""
    tag = str(route.tags[0]) if route.tags else "default"
    return f"{tag}-{route.name}"


def create_app(container: AppContainer | None = None) -> FastAPI:
    """Build the configured FastAPI application. Pass `container` explicitly in
    tests; production resolves it from the environment."""
    if container is not None:
        set_container(container)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Threadpool routers and the relay thread publish from off-loop; the
        # broker needs the loop to hop onto it safely.
        get_broker().bind_loop(asyncio.get_running_loop())

        stop_event = threading.Event()
        relay = threading.Thread(
            target=run_outbox_relay,
            args=(get_container().session_factory, get_broker(), stop_event.is_set),
            daemon=True,
            name="outbox-relay",
        )
        relay.start()
        log.info("api.started", version=_API_VERSION)
        yield
        stop_event.set()
        relay.join(timeout=5.0)
        log.info("api.stopped")

    app = FastAPI(
        title="AIPOM Orchestrator API",
        version=_API_VERSION,
        description=(
            "RESTful API for the AIPOM AI project orchestrator: the 9-phase "
            "plan lifecycle (discovery, architecture, enriching, the two human "
            "gates, execution, the replan loop), reference-data catalogs, "
            "two-tier config, and the live SSE event stream."
        ),
        generate_unique_id_function=_unique_operation_id,
        lifespan=lifespan,
    )

    configure_logging()
    register_exception_handlers(app)

    # Request logging (correlation id) before CORS so the id contextvar covers
    # CORS-handled responses too.
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    _prefix = "/api"
    app.include_router(plans.router, prefix=_prefix)
    app.include_router(reference.router, prefix=_prefix)
    app.include_router(config.router, prefix=_prefix)
    app.include_router(reasoner.router, prefix=_prefix)
    app.include_router(runner.router, prefix=_prefix)
    app.include_router(metrics.router, prefix=_prefix)
    app.include_router(events.router, prefix=_prefix)

    @app.get(
        "/health",
        response_model=HealthResponse,
        tags=["system"],
        summary="Health Check",
    )
    def health() -> HealthResponse:
        return HealthResponse(status="ok", version=_API_VERSION)

    return app
