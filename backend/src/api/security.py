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

from fastapi import Header

from src.infra.errors import UnauthorizedError

API_TOKEN_ENV = "ORCHESTRATOR_API_TOKEN"


def require_api_token(
    authorization: str | None = Header(default=None),
    x_api_token: str | None = Header(default=None),
) -> None:
    expected = os.environ.get(API_TOKEN_ENV, "").strip()
    if not expected:
        return  # open in local dev
    provided = x_api_token
    if not provided and authorization and authorization.startswith("Bearer "):
        provided = authorization[len("Bearer "):].strip()
    if provided != expected:
        raise UnauthorizedError(
            "Missing or invalid API token", code="INVALID_API_TOKEN"
        )
