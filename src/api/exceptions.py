"""
src/api/exceptions.py — Global Domain → HTTP exception mappings.

Register all handlers in ``register_exception_handlers(app)``.  Routers
stay free of try/except blocks; they simply call the use case and let
exceptions bubble up to these handlers.
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.api.schemas.common import ErrorResponse

# Domain imports — only error types, never aggregates or use cases
from src.domain.errors import (
    DomainError,
    InvalidStatusTransitionError,
    MaxRetriesExceededError,
    ForbiddenFileEditError,
)
from src.domain.project_spec.errors import (
    SpecNotFoundError,
    SpecValidationError,
    SpecVersionMismatchError,
    ForbiddenMutationError,
)


def _error_body(detail: str) -> dict:
    return ErrorResponse(detail=detail).model_dump()


def register_exception_handlers(app: FastAPI) -> None:
    """Attach all domain-to-HTTP exception handlers to *app*."""

    # ── 404 Not Found ─────────────────────────────────────────────────────────
    @app.exception_handler(KeyError)
    async def key_error_handler(request: Request, exc: KeyError) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content=_error_body(f"Resource not found: {exc}"),
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
