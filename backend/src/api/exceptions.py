"""
src/api/exceptions.py — Global Domain → HTTP exception mappings.

Register all handlers in ``register_exception_handlers(app)``.  Routers
stay free of try/except blocks; they simply call the use case and let
exceptions bubble up to these handlers.
"""
from __future__ import annotations

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.api.middleware.request_logging import get_request_id
from src.api.schemas.common import ErrorEnvelope, ErrorResponse, PlanConflictResponse

# App-level exception taxonomy
from src.app.errors import (
    ExternalServiceException,
    ForbiddenException,
    InfrastructureException,
    ResourceNotFoundException,
    UnauthorizedException,
    ValidationException,
)

# Domain imports — only error types, never aggregates or use cases
from src.domain.errors import (
    BaseAppException,
    ConflictException,
    DomainError,
    InvalidPlanTransitionError,
    InvalidStatusTransitionError,
    MaxRetriesExceededError,
    ForbiddenFileEditError,
    ReferentialException,
)
from src.domain.project_spec.errors import (
    SpecNotFoundError,
    SpecValidationError,
    SpecVersionMismatchError,
    ForbiddenMutationError,
)

# Infra error — raised when the active project context cannot be resolved.
from src.infra.settings.models import ConfigurationError

log = structlog.get_logger("api.exceptions")


def _error_body(detail: str) -> dict:
    return ErrorResponse(detail=detail).model_dump()


def _envelope(code: str, message: str) -> dict:
    """Build the consistent control-plane error body with the correlation id."""
    return ErrorEnvelope.model_validate(
        {"error": {"code": code, "message": message, "request_id": get_request_id()}}
    ).model_dump()


def register_exception_handlers(app: FastAPI) -> None:
    """Attach all domain-to-HTTP exception handlers to *app*."""

    # ── 400 Bad Request — unresolved project context ──────────────────────────
    @app.exception_handler(ConfigurationError)
    async def configuration_error_handler(
        request: Request, exc: ConfigurationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content=_error_body(str(exc)),
        )

    # ── 404 Not Found ─────────────────────────────────────────────────────────
    @app.exception_handler(KeyError)
    async def key_error_handler(request: Request, exc: KeyError) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content=_error_body(f"Resource not found: {exc}"),
        )

    # ── 409 Conflict — invalid plan lifecycle transition ──────────────────────
    @app.exception_handler(InvalidPlanTransitionError)
    async def invalid_plan_transition_handler(
        request: Request, exc: InvalidPlanTransitionError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=PlanConflictResponse(
                detail=str(exc),
                action=exc.action,
                current_status=exc.current_status,
                expected_status=exc.expected,
            ).model_dump(),
        )

    # ── 409 Conflict — invalid state transition ───────────────────────────────
    @app.exception_handler(InvalidStatusTransitionError)
    async def invalid_transition_handler(
        request: Request, exc: InvalidStatusTransitionError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=_error_body(str(exc)),
        )

    # ── 409 Conflict — retries exhausted ─────────────────────────────────────
    @app.exception_handler(MaxRetriesExceededError)
    async def max_retries_handler(
        request: Request, exc: MaxRetriesExceededError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=_error_body(str(exc)),
        )

    # ── 409 Conflict — generic ValueError from domain ─────────────────────────
    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=_error_body(str(exc)),
        )

    # ── 422 Unprocessable — forbidden file edits ──────────────────────────────
    @app.exception_handler(ForbiddenFileEditError)
    async def forbidden_edit_handler(
        request: Request, exc: ForbiddenFileEditError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_error_body(f"Forbidden file modifications: {exc.violations}"),
        )

    # ── 422 Unprocessable — forbidden spec mutation ───────────────────────────
    @app.exception_handler(ForbiddenMutationError)
    async def forbidden_mutation_handler(
        request: Request, exc: ForbiddenMutationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_error_body(str(exc)),
        )

    # ── 404 Not Found — spec not found ────────────────────────────────────────
    @app.exception_handler(SpecNotFoundError)
    async def spec_not_found_handler(
        request: Request, exc: SpecNotFoundError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content=_error_body(str(exc)),
        )

    # ── 422 Unprocessable — spec validation failure ───────────────────────────
    @app.exception_handler(SpecValidationError)
    async def spec_validation_handler(
        request: Request, exc: SpecValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_error_body(str(exc)),
        )

    # ── 409 Conflict — spec version mismatch ─────────────────────────────────
    @app.exception_handler(SpecVersionMismatchError)
    async def spec_version_handler(
        request: Request, exc: SpecVersionMismatchError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=_error_body(str(exc)),
        )

    # ── 500 catch-all for any unhandled DomainError ───────────────────────────
    @app.exception_handler(DomainError)
    async def domain_error_handler(
        request: Request, exc: DomainError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=_error_body(f"Internal domain error: {type(exc).__name__}"),
        )

    # ======================================================================
    # Control-plane taxonomy (BaseAppException tree) -> envelope + request_id
    # This is the ONLY place exception->HTTP translation happens for these.
    # ======================================================================

    def _make_handler(status_code: int):
        async def handler(request: Request, exc: BaseAppException) -> JSONResponse:
            # Expected, typed errors: log at warning with the stable code and
            # safe context — never a stack trace, never to the client.
            log.warning(
                "request_error",
                code=exc.code,
                message=exc.message,
                status_code=status_code,
                path=request.url.path,
                context=exc.context,
            )
            return JSONResponse(
                status_code=status_code,
                content=_envelope(exc.code, exc.message),
            )
        return handler

    app.add_exception_handler(ResourceNotFoundException, _make_handler(404))
    app.add_exception_handler(ValidationException, _make_handler(400))
    app.add_exception_handler(ConflictException, _make_handler(409))
    app.add_exception_handler(ReferentialException, _make_handler(409))
    app.add_exception_handler(UnauthorizedException, _make_handler(401))
    app.add_exception_handler(ForbiddenException, _make_handler(403))
    app.add_exception_handler(ExternalServiceException, _make_handler(502))
    app.add_exception_handler(InfrastructureException, _make_handler(503))
    # Any other app-level exception that slipped through -> 500 (still enveloped).
    app.add_exception_handler(BaseAppException, _make_handler(500))

    # ── 500 catch-all for truly unhandled exceptions ──────────────────────────
    # Full detail (type, message, stack trace, endpoint, request_id) is logged
    # internally only; the client gets a generic enveloped 500 — never a stack
    # trace, never a bare framework error page.
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
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
