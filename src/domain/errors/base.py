"""
src/domain/errors/base.py — Base domain error.
"""
from __future__ import annotations


class DomainError(Exception):
    """
    Base class for all domain errors.
    Subclass this for any exception that represents a domain rule violation.
    """
