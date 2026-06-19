"""
src/domain/errors/base.py — Base exception hierarchy.

A single exception language spans the whole system. ``BaseAppException`` is the
common root: every typed error carries a stable string ``code`` (e.g.
``PROJECT_NOT_FOUND``) and a human ``message``. The code never encodes HTTP
status — the API layer is the only place that maps codes to HTTP responses, so
the domain stays ignorant of the transport.

``DomainError`` remains the root for domain-rule violations (existing subclasses
like ``InvalidStatusTransitionError`` keep working unchanged). ``DomainException``
is provided as a readable alias. Concurrency/referential failures from the
persistence layer (``ConflictException``, ``ReferentialException``) live in
``config_errors`` and subclass ``DomainError`` so they speak the same language.
"""
from __future__ import annotations

from typing import Any


class BaseAppException(Exception):
    """
    Common root for every typed application error.

    Subclasses set a class-level ``code``; callers may override it per-instance.
    ``context`` carries small, log-safe metadata (never secrets, never large
    payloads).
    """

    code: str = "INTERNAL_ERROR"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.message = message
        if code is not None:
            self.code = code
        self.context: dict[str, Any] = context or {}
        super().__init__(message)


class DomainError(BaseAppException):
    """
    Base class for all domain-rule violations.

    Subclass this for any exception that represents a domain invariant breach.
    """

    code = "DOMAIN_ERROR"


# Readable alias used by newer code; identical to DomainError.
DomainException = DomainError
