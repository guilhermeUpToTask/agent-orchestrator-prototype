"""Config -> ProviderCapacityPolicy, and the values an operator must not be able
to wedge execution with.

The config store returns STRINGS, which is why this needed its own guard: the
factory's `or` fallback sees `"0"` — truthy — and passed a literal zero cap
through to an admission gate that then declined every attempt with nothing in
flight, opening no circuit and no block.
"""

from __future__ import annotations

import pytest

from src.app.provider_capacity import ProviderCapacityPolicy
from src.infra.policies.provider_capacity_factory import build_provider_capacity_policy


class _ConfigStore:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def get(self, scope: str, key: str) -> str | None:
        return self._values.get(key)


def test_a_configured_cap_is_used():
    policy = build_provider_capacity_policy(
        _ConfigStore({"execution.provider_max_inflight": "3"})  # type: ignore[arg-type]
    )
    assert policy.max_inflight == 3


def test_an_unset_cap_uses_the_default():
    policy = build_provider_capacity_policy(_ConfigStore({}))  # type: ignore[arg-type]
    assert policy.max_inflight == ProviderCapacityPolicy().max_inflight


@pytest.mark.parametrize("configured", ["0", "-1"])
def test_a_non_positive_configured_cap_falls_back_to_the_default(configured):
    """`"0"` is a truthy string, so the `or` fallback never fired for it."""
    policy = build_provider_capacity_policy(
        _ConfigStore({"execution.provider_max_inflight": configured})  # type: ignore[arg-type]
    )
    assert policy.max_inflight == ProviderCapacityPolicy().max_inflight
