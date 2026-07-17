"""
src/api/exceptions.py — the ONE error -> HTTP mapping layer (roadmap 4.1).

Routers stay free of try/except: they call use cases and let typed errors
bubble here. Every DomainError carries a stable `code`; the table below maps
codes to statuses, so adding an error type is one line — never a new handler.

  404  not-found          PLAN/GOAL/TASK/AGENT/MODEL/PROVIDER/CAPABILITY/SECRET
  409  conflict           STALE_VERSION, GOAL_ALREADY_RUNNING, ENTITY_IN_USE,
                          ENTITY_ALREADY_EXISTS  (+ PLAN_BUSY/TASK_RUNNING when
                          the roadmap 3.5 guards land)
  422  unprocessable      INVALID_EDIT, EMPTY_PLAN, INVALID_TRANSITION,
                          PLAN_ALREADY_TERMINAL, UNKNOWN_CAPABILITY, ...
  400  any other DomainError (malformed request against the domain)
  401  UNAUTHORIZED
  503  InfrastructureError (except SECRET_NOT_FOUND -> 404)
  500  anything unhandled — generic envelope, stack trace logged only

There is deliberately NO blanket KeyError/ValueError mapping: an unmapped
builtin error is a bug and should surface as the enveloped 500.
"""

from __future__ import annotations

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.api.middleware.request_logging import get_request_id
from src.api.schemas.common import ErrorEnvelope
from src.domain.errors.base import DomainError
from src.infra.errors import InfrastructureError, UnauthorizedError

log = structlog.get_logger("api.exceptions")

_STATUS_BY_CODE: dict[str, int] = {
    # 404 — not found
    "PLAN_NOT_FOUND": 404,
    "GOAL_NOT_FOUND": 404,
    "TASK_NOT_FOUND": 404,
    "AGENT_NOT_FOUND": 404,
    "MODEL_NOT_FOUND": 404,
    "PROVIDER_NOT_FOUND": 404,
    "CAPABILITY_NOT_FOUND": 404,
    "SECRET_NOT_FOUND": 404,
    # 409 — conflict
    "STALE_VERSION": 409,
    "GOAL_ALREADY_RUNNING": 409,
    "ENTITY_IN_USE": 409,
    "ENTITY_ALREADY_EXISTS": 409,
    "PLAN_BUSY": 409,
    "TASK_RUNNING": 409,
    # 422 — domain rules rejected the content
    "INVALID_EDIT": 422,
    "EMPTY_PLAN": 422,
    "INVALID_TRANSITION": 422,
    "PLAN_ALREADY_TERMINAL": 422,
    "UNKNOWN_CAPABILITY": 422,
    "CAPABILITY_NO_LONGER_SATISFIED": 422,
    "NO_DEFAULT_AGENT": 422,
    "REASONER_CONFIG_INVALID": 422,
    "AGENT_RUNNER_CONFIG_INVALID": 422,
    # 502 — upstream provider failed (rate limit / out of credits / upstream error).
    # The chat path (DISCOVERY/REPLANNING) surfaces reasoner failures through this;
    # worker-phase reasoner failures surface via the ReasonerFailed SSE event.
    "REASONER_FAILED": 502,
}
_DEFAULT_DOMAIN_STATUS = 400
_DEFAULT_INFRA_STATUS = 503


def _envelope(code: str, message: str) -> dict:
    return ErrorEnvelope.model_validate(
        {"error": {"code": code, "message": message, "request_id": get_request_id()}}
    ).model_dump()


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(UnauthorizedError)
    async def unauthorized_handler(request: Request, exc: UnauthorizedError) -> JSONResponse:
        return JSONResponse(status_code=401, content=_envelope(exc.code, exc.message))

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
        status = _STATUS_BY_CODE.get(exc.code, _DEFAULT_DOMAIN_STATUS)
        log.warning(
            "request_error",
            code=exc.code,
            message=exc.message,
            status_code=status,
            path=request.url.path,
            context=exc.context,  # log-safe by contract; never secrets
        )
        return JSONResponse(status_code=status, content=_envelope(exc.code, exc.message))

    @app.exception_handler(InfrastructureError)
    async def infrastructure_error_handler(
        request: Request, exc: InfrastructureError
    ) -> JSONResponse:
        status = _STATUS_BY_CODE.get(exc.code, _DEFAULT_INFRA_STATUS)
        log.warning(
            "request_infra_error",
            code=exc.code,
            status_code=status,
            path=request.url.path,
        )
        return JSONResponse(status_code=status, content=_envelope(exc.code, exc.message))

    # Full detail (type, stack trace, endpoint, request_id) is logged
    # internally only; the client gets a generic envelope — never a stack
    # trace, never a bare framework error page.
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        log.error(
            "request_unhandled_error",
            exc_type=type(exc).__name__,
            path=request.url.path,
            request_id=get_request_id(),
            exc_info=exc,
        )
        return JSONResponse(
            status_code=500,
            content=_envelope("INTERNAL_ERROR", "An internal error occurred"),
        )
