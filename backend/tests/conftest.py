"""Test bootstrap: make the backend root importable so `praxis_orchestrator.*`
resolves without an editable install."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# The pre-rename environment variables, which `praxis_orchestrator/infra/env_compat.py`
# still honours. Every machine that installed before Phase 10B exports them —
# the dev guest included — and that is exactly what makes them dangerous here.
_PRE_RENAME_NAMES = (
    "ORCHESTRATOR_HOME",
    "ORCHESTRATOR_MASTER_KEY",
    "ORCHESTRATOR_API_TOKEN",
    "ORCHESTRATOR_DB_URL",
)


@pytest.fixture(autouse=True, scope="session")
def _no_ambient_pre_rename_env() -> None:
    """Strip the legacy names for the whole session.

    Twenty-one tests clear `PRAXIS_*` to assert what happens with no key or no
    token — an open API, a fail-closed secret store, a readiness check that
    says "not needed". Since the alias exists, clearing only the new name no
    longer produces the state those tests describe: the value arrives through
    the old one and the assertion quietly changes meaning. Two of them failed
    outright on the guest, which is how this was found; the rest passed for
    reasons unrelated to what they claim to test.

    Clearing once here fixes the class rather than twenty-one instances of it.
    Tests that want a legacy variable set it explicitly with `monkeypatch`,
    which still works — see `tests/integration/test_rename_compatibility.py`.
    """
    for name in _PRE_RENAME_NAMES:
        os.environ.pop(name, None)
