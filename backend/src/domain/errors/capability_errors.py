"""
src/domain/errors/capability_errors.py — Capability domain errors.
"""
from __future__ import annotations

from src.domain.errors.base import DomainError


class UnknownCapabilityError(DomainError):
    """Raised when a capability tag is not registered in the CapabilityRegistry."""

    def __init__(self, tag: str, known: list[str]) -> None:
        self.tag = tag
        self.known = known
        known_str = ", ".join(known) if known else "(none registered)"
        super().__init__(
            f"Unknown capability '{tag}'. Register it first "
            f"(orchestrate capabilities add '{tag}') or use a known tag: {known_str}."
        )
