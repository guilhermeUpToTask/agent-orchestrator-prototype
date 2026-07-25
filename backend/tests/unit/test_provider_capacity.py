"""Which circuit a capacity failure belongs to, and the block evidence-ref codec."""

from __future__ import annotations

import pytest

from src.app.provider_capacity import (
    CapacityScope,
    circuit_model_id,
    circuit_ref,
    parse_circuit_ref,
)
from src.app.runtime_failures import LimitScope


@pytest.mark.parametrize(
    "scope",
    [LimitScope.QUOTA, LimitScope.DAILY_QUOTA],
)
def test_account_level_limits_key_provider_wide(scope):
    """Spend caps and daily allowances are shared by every model on the key, so
    routing to a sibling model must not escape the circuit."""
    assert circuit_model_id("nemotron", scope) is None


@pytest.mark.parametrize(
    "scope",
    [LimitScope.REQUEST_CONCURRENCY, LimitScope.UNKNOWN_CAPACITY, None],
)
def test_upstream_level_limits_key_per_model(scope):
    """One saturated upstream pool must not throttle the other models on the same
    key. An unclassified scope takes this narrower key deliberately: guessing
    'account-wide' from an unrecognized message would halt every sibling model."""
    assert circuit_model_id("nemotron", scope) == "nemotron"


def test_endpoint_wide_provider_shares_one_concurrency_circuit():
    """A single-endpoint deployment serves every model from one pool, so its
    concurrency limits are provider-wide too. Expressed as provider metadata --
    never by branching on a provider name."""
    assert (
        circuit_model_id(
            "nemotron",
            LimitScope.REQUEST_CONCURRENCY,
            CapacityScope.ENDPOINT_WIDE,
        )
        is None
    )


def test_endpoint_wide_still_keys_account_limits_provider_wide():
    assert (
        circuit_model_id("nemotron", LimitScope.DAILY_QUOTA, CapacityScope.ENDPOINT_WIDE) is None
    )


@pytest.mark.parametrize("model_id", ["nemotron", None])
def test_circuit_ref_round_trips(model_id):
    """`wait_and_retry` clears the circuit named by the block's evidence ref, so a
    provider-wide circuit must survive the string round trip as None. Encoding it
    in a way that parses back to a literal token would clear nothing and leave the
    operator's retry silently ineffective."""
    ref = circuit_ref("pi", "openrouter", model_id)
    assert parse_circuit_ref(ref) == ("pi", "openrouter", model_id)


def test_circuit_ref_is_a_stable_uri():
    assert circuit_ref("pi", "openrouter", "nemotron") == (
        "runtime-circuit://pi/openrouter/nemotron"
    )


def test_parse_rejects_a_malformed_ref():
    with pytest.raises(ValueError):
        parse_circuit_ref("runtime-circuit://only-one-segment")
