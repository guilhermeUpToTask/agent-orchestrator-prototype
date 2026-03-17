"""
src/app/reconciliation/ — Reconciliation engine package.

Phase 1 of the orchestration refactoring: the Reconciler has been extracted
from the monolithic src/app/reconciler.py into this package so its single
responsibility (watchdog loop) is clearly separated from scheduling,
assignment, and retry policies that live in core/services.py.
"""

from src.app.reconciliation.reconciliation_engine import Reconciler

__all__ = ["Reconciler"]
