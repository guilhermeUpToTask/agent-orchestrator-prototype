"""
src/api/security.py — control-plane authentication.

Prototype-grade single shared token, structured so real auth (per-user tokens,
OAuth) can replace it without touching routers. When ``ORCHESTRATOR_API_TOKEN``
is unset the control plane is open (local dev); when set, every control-plane
request must present it via ``Authorization: Bearer <token>`` or ``X-API-Token``.
Failures raise the shared taxonomy (-> 401), never a bare framework error.
"""

from __future__ import annotations

import os

from fastapi import Header, Query

from src.infra.errors import UnauthorizedError

API_TOKEN_ENV = "ORCHESTRATOR_API_TOKEN"


def _expected_token() -> str:
    return os.environ.get(API_TOKEN_ENV, "").strip()


def _from_headers(authorization: str | None, x_api_token: str | None) -> str | None:
    if x_api_token:
        return x_api_token
    if authorization and authorization.startswith("Bearer "):
        return authorization[len("Bearer ") :].strip()
    return None


def require_api_token(
    authorization: str | None = Header(default=None),
    x_api_token: str | None = Header(default=None),
) -> None:
    """The guard for every operation. Applied once at mount time in
    `server.py::create_app`, never declared per router — an opt-in guard is one
    a router can forget, which is exactly what the Phase 3 audit found."""
    expected = _expected_token()
    if not expected:
        return  # open in local dev
    if _from_headers(authorization, x_api_token) != expected:
        raise UnauthorizedError("Missing or invalid API token", code="INVALID_API_TOKEN")


def require_api_token_or_query(
    authorization: str | None = Header(default=None),
    x_api_token: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> None:
    """Header auth, plus `?token=` for the ONE route a browser opens directly.

    `EventSource` cannot set request headers (`frontend/src/lib/api.ts`), so the
    SSE stream would otherwise be unguardable without inventing a session
    cookie. Confining the URL-borne token to this route keeps the tradeoff
    visible and testable: no other operation accepts it.

    The query string is never written down — `api start` runs uvicorn with
    `access_log=False`, and the structured request log records `url.path` only
    (`middleware/request_logging.py`).
    """
    expected = _expected_token()
    if not expected:
        return  # open in local dev
    provided = _from_headers(authorization, x_api_token) or token
    if provided != expected:
        raise UnauthorizedError("Missing or invalid API token", code="INVALID_API_TOKEN")
