"""
src/api/middleware/request_logging.py — correlation id + request lifecycle logs.

A correlation id (``request_id``) is generated per request (or taken from an
inbound ``X-Request-ID``), stored in a contextvar so handlers, loggers, and the
exception layer can read it without threading it through call signatures, and
echoed back in the response header.

``request_started`` / ``request_finished`` are emitted at a configurable level
(REQUEST_LOG_LEVEL, default info; set ``REQUEST_LOG=0`` to silence) — exception
logs, by contrast, are always-on and live in the exception handlers.
"""

from __future__ import annotations

import os
import time
import uuid
from contextvars import ContextVar

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

log = structlog.get_logger("api.request")

REQUEST_ID_HEADER = "X-Request-ID"

# Default "-" so logs emitted outside a request (startup, coordinators) are valid.
_request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


def get_request_id() -> str:
    """Return the current request's correlation id, or '-' outside a request."""
    return _request_id_var.get()


def set_request_id(value: str) -> None:
    """Bind a correlation id on the current context (e.g. background work)."""
    _request_id_var.set(value)


def _request_logging_enabled() -> bool:
    return os.environ.get("REQUEST_LOG", "1") != "0"


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        token = _request_id_var.set(request_id)
        enabled = _request_logging_enabled()
        start = time.perf_counter()
        if enabled:
            log.info(
                "request_started",
                request_id=request_id,
                method=request.method,
                path=request.url.path,
                client_ip=request.client.host if request.client else None,
            )
        try:
            response = await call_next(request)
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            _request_id_var.reset(token)
        if enabled:
            log.info(
                "request_finished",
                request_id=request_id,
                status_code=response.status_code,
                duration_ms=duration_ms,
            )
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
