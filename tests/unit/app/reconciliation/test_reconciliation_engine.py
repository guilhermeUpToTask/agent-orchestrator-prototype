"""
tests/unit/app/reconciliation/test_reconciliation_engine.py

Verifies that the Reconciler lives in its canonical package location.
"""
from __future__ import annotations

from src.app.reconciliation import Reconciler


def test_reconciler_importable_from_package():
    assert Reconciler is not None


def test_reconciler_can_be_instantiated():
    from unittest.mock import MagicMock
    r = Reconciler(
        task_repo=MagicMock(),
        lease_port=MagicMock(),
        event_port=MagicMock(),
        agent_registry=MagicMock(),
    )
    assert r is not None
