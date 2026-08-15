from __future__ import annotations

from typing import Any


class BaseAppException(Exception):
    """Common root for every typed application error.

    Subclasses set a class-level ``code``; callers may override per-instance.
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
    """Base class for all domain-rule violations."""

    code = "DOMAIN_ERROR"
