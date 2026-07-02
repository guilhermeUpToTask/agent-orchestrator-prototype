"""Infrastructure-layer errors.

The domain owns DomainError (business-rule violations); this module owns the
failures of the machinery itself (database locked, adapter I/O). Kept in infra —
neither domain nor app may depend on it.
"""
from __future__ import annotations

from src.domain.errors.base import BaseAppException


class InfrastructureError(BaseAppException):
    """An infrastructure operation failed (DB, filesystem, subprocess, network)."""

    code = "INFRASTRUCTURE_ERROR"
