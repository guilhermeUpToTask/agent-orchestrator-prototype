"""
src/domain/errors/config_errors.py — Persistence-boundary domain errors.

These represent invariant violations surfaced by the config/state stores:

  ConflictException     — optimistic-concurrency (state_version CAS) failure.
  ReferentialException  — an entity is still referenced and cannot be removed,
                          or references a non-existent parent (FK violation).

Both are DomainError subclasses so the whole system speaks one exception
language; the API layer maps them to 409 (and 409/422 respectively).
"""
from __future__ import annotations

from typing import Any

from src.domain.errors.base import DomainError


class ConflictException(DomainError):
    """
    Raised when a compare-and-swap write fails because the stored
    ``state_version`` no longer matches the expected version. The caller should
    reload the aggregate and retry, or surface a 409 to the client.
    """

    code = "CONFLICT"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        expected_version: int | None = None,
        actual_version: int | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        ctx: dict[str, Any] = dict(context or {})
        if expected_version is not None:
            ctx.setdefault("expected_version", expected_version)
        if actual_version is not None:
            ctx.setdefault("actual_version", actual_version)
        self.expected_version = expected_version
        self.actual_version = actual_version
        super().__init__(message, code=code, context=ctx)


class ReferentialException(DomainError):
    """
    Raised when a referential-integrity constraint is violated — e.g. deleting a
    provider that agents still reference, or creating an agent that points at a
    missing provider.
    """

    code = "REFERENTIAL_CONSTRAINT"
