"""
agent_orchestrator/api/exceptions.py — the ONE error -> HTTP mapping layer (roadmap 4.1).

Routers stay free of try/except: they call use cases and let typed errors
bubble here. Every DomainError carries a stable `code`; the table below maps
codes to statuses, so adding an error type is one line — never a new handler.

  404  not-found          PLAN/GOAL/TASK/AGENT/MODEL/PROVIDER/CAPABILITY/SECRET
  409  conflict           STALE_VERSION, GOAL_ALREADY_RUNNING, ENTITY_IN_USE,
                          ENTITY_ALREADY_EXISTS  (+ PLAN_BUSY/TASK_RUNNING when
                          the roadmap 3.5 guards land)
  422  unprocessable      INVALID_EDIT, EMPTY_PLAN, INVALID_TRANSITION,
                          PLAN_ALREADY_TERMINAL, UNKNOWN_CAPABILITY, ...
                          plus VALIDATION_ERROR — the SCHEMA rejection, see below
  400  any other DomainError (malformed request against the domain)
  401  UNAUTHORIZED
  503  InfrastructureError (except SECRET_NOT_FOUND -> 404)
  500  anything unhandled — generic envelope, stack trace logged only

There is deliberately NO blanket KeyError/ValueError mapping: an unmapped
builtin error is a bug and should surface as the enveloped 500.

`RequestValidationError` is registered here too, and it is not a formality
(Phase 10A). FastAPI's default handler answers `{"detail": [...]}` — outside
this envelope, so the console's `errorDetail` could not read it — and each entry
carries an `input` field holding **the value that failed**. For a `missing`
error that value is the WHOLE submitted body, so `POST /api/providers` without a
`name` echoed the plaintext `api_key` straight back to the caller, and the
console rendered it into a toast. The handler below reports the failing
locations and never a submitted value.
"""

from __future__ import annotations

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from agent_orchestrator.api.middleware.request_logging import get_request_id
from agent_orchestrator.api.schemas.common import ErrorEnvelope
from agent_orchestrator.app.forge_port import ForgeError
from agent_orchestrator.domain.errors.base import DomainError
from agent_orchestrator.infra.errors import InfrastructureError, UnauthorizedError

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
    "ATTEMPT_NOT_FOUND": 404,
    "CYCLE_NOT_FOUND": 404,
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
    "ROLE_UNSATISFIABLE": 422,
    "NO_DEFAULT_AGENT": 422,
    "REASONER_CONFIG_INVALID": 422,
    "AGENT_RUNNER_CONFIG_INVALID": 422,
    "PROJECT_BINDING_INVALID": 422,
    # Forge (P8.1): a configuration or credential problem the operator can fix.
    "FORGE_NOT_CONFIGURED": 422,
    "FORGE_AUTH_FAILED": 422,
    "FORGE_REPO_NOT_FOUND": 422,
    # 502 — upstream provider failed (rate limit / out of credits / upstream error).
    # The chat path (DISCOVERY/REPLANNING) surfaces reasoner failures through this;
    # worker-phase reasoner failures surface via the ReasonerFailed SSE event.
    "REASONER_FAILED": 502,
    # Forge (P8.1): the remote was reached and refused, or could not be reached.
    "FORGE_PUSH_FAILED": 502,
    "FORGE_REQUEST_FAILED": 502,
    # The review surface could not read a diff the evidence says exists.
    "REVIEW_DIFF_UNAVAILABLE": 502,
}
_DEFAULT_DOMAIN_STATUS = 400
_DEFAULT_INFRA_STATUS = 503


def _envelope(code: str, message: str) -> dict:
    return ErrorEnvelope.model_validate(
        {"error": {"code": code, "message": message, "request_id": get_request_id()}}
    ).model_dump()


def _location(error: dict) -> str:
    """`("body", "name")` -> `body.name`. Locations only — never `error["input"]`,
    which is the value the caller sent and may be a credential."""
    parts = [str(part) for part in error.get("loc", ())]
    return ".".join(parts) if parts else "(request)"


def _validation_message(errors: list[dict]) -> str:
    """One operator-readable line naming what was wrong and where.

    Pydantic's `msg` describes the RULE ("Field required", "Input should be
    greater than or equal to 1"); it never contains the submitted value, so it
    is safe to pass through. `input` and `ctx` are dropped.
    """
    parts = [f"{_location(error)}: {error.get('msg', 'invalid')}" for error in errors[:10]]
    if len(errors) > 10:
        parts.append(f"(+{len(errors) - 10} more)")
    return "Request validation failed — " + "; ".join(parts)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Schema rejection, in the one envelope, with no submitted values.

        Registered so a 422 from FastAPI is shaped like every other error the
        API returns. See the module docstring for the disclosure this closes.
        """
        errors = [dict(error) for error in exc.errors()]
        log.warning(
            "request_validation_error",
            path=request.url.path,
            locations=[_location(error) for error in errors],  # never the values
            error_count=len(errors),
        )
        return JSONResponse(
            status_code=422,
            content=_envelope("VALIDATION_ERROR", _validation_message(errors)),
        )

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

    @app.exception_handler(ForgeError)
    async def forge_error_handler(request: Request, exc: ForgeError) -> JSONResponse:
        """A forge failure is a coded error like any other, not a 500.

        ForgeError subclasses BaseAppException rather than DomainError (the
        frozen domain never hears about forges) or InfrastructureError (it
        lives in app/, beside the port), so it needs its own registration to
        reach the one status table instead of the generic handler.
        """
        status = _STATUS_BY_CODE.get(exc.code, _DEFAULT_INFRA_STATUS)
        log.warning(
            "request_forge_error",
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
