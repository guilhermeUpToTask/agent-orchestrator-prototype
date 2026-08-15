"""Which circuit a capacity failure belongs to, and the block evidence-ref codec."""

from __future__ import annotations

import pytest

from praxis_orchestrator.app.provider_capacity import (
    CapacityScope,
    ProviderCapacityPolicy,
    capacity_backoff_seconds,
    circuit_model_id,
    circuit_ref,
    parse_circuit_ref,
    resolve_max_inflight,
)
from praxis_orchestrator.app.runtime_failures import LimitScope
from praxis_orchestrator.domain.policies.retry_policies import RetryPolicy
from praxis_orchestrator.domain.value_objects.lifecycle import FailureKind


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


def _dt(seconds: float):
    from datetime import datetime, timedelta, timezone

    return datetime(2026, 7, 25, tzinfo=timezone.utc) + timedelta(seconds=seconds)


def test_outage_start_continues_an_in_progress_outage():
    """Within one outage the start must be preserved, or the age pegs at ~0 and the
    ceiling is never reachable."""
    policy = ProviderCapacityPolicy()
    opened, retry_at, now = _dt(0), _dt(120), _dt(180)
    assert policy.outage_start(opened, retry_at, now) == opened


def test_outage_start_resets_after_a_long_quiet_period():
    """Regression for a live finding: a circuit row abandoned by an earlier session
    (retry_at three days in the past) pre-aged the FIRST failure of a fresh outage
    past the ceiling, escalating immediately -- the premature escalation this whole
    design exists to remove."""
    policy = ProviderCapacityPolicy()
    stale_opened, stale_retry = _dt(0), _dt(5)
    now = _dt(3 * 24 * 3_600)
    assert policy.outage_start(stale_opened, stale_retry, now) == now
    # and therefore does not read as an outage older than the ceiling
    assert not policy.outage_exceeded(
        policy.outage_start(stale_opened, stale_retry, now), now, None
    )


def test_outage_start_treats_a_missing_circuit_as_a_new_outage():
    assert ProviderCapacityPolicy().outage_start(None, None, _dt(50)) == _dt(50)


def test_a_daily_quota_waiting_normally_is_not_mistaken_for_a_new_outage():
    """The quiet threshold must exceed anything the policy itself waits. A daily
    quota backs off an hour at a time, so a fixed 1h reset would have reset its own
    window every cycle and stopped it from ever escalating."""
    policy = ProviderCapacityPolicy()
    opened = _dt(0)
    retry_at = _dt(3_600)  # the daily-quota backoff floor
    now = _dt(3_600 + 3_500)  # ~1h of quiet: still just waiting
    assert policy.outage_start(opened, retry_at, now, "daily_quota") == opened


def test_an_outage_old_enough_to_escalate_is_not_reset_by_its_own_age():
    """The reset must not contradict escalation: reaching a ceiling necessarily
    involves a long wait, so keying the reset on the scope's own ceiling made every
    escalatable outage look like a fresh one."""
    policy = ProviderCapacityPolicy(outage_ceiling_seconds=100)
    opened, retry_at = _dt(0), _dt(10)
    now = _dt(500)  # far past a 100s ceiling, but nowhere near a day
    assert policy.outage_start(opened, retry_at, now, None) == opened
    assert policy.outage_exceeded(opened, now, None)


# --- per-limit_scope backoff curves ------------------------------------------

_CURVE = RetryPolicy(
    initial_backoff_seconds=30,
    backoff_multiplier=2,
    max_backoff_seconds=900,
    jitter_ratio=0,
)


def test_a_concurrency_refusal_uses_the_plans_ordinary_curve():
    """`kind_backoff_scale` grants RATE_LIMIT a 4x patient curve because an
    exhausted account allowance needs a long wait. A concurrency refusal on a
    SHARED pool means the opposite -- 'someone else is using it right now' -- and
    is the case that opens no circuit and requeues just this task, so it must not
    ride the patient curve."""
    assert (
        capacity_backoff_seconds(
            _CURVE,
            2,
            kind=FailureKind.RATE_LIMIT,
            limit_scope=LimitScope.REQUEST_CONCURRENCY,
        )
        == 30.0
    )


@pytest.mark.parametrize(
    "scope",
    [LimitScope.QUOTA, LimitScope.DAILY_QUOTA, LimitScope.UNKNOWN_CAPACITY, None],
)
def test_every_other_scope_keeps_the_patient_rate_limit_curve(scope):
    """Only a POSITIVELY identified concurrency refusal is impatient. An
    unclassified message degrades to patience for the same reason it degrades to
    the narrower circuit key: the harmful mistake is the aggressive one."""
    assert capacity_backoff_seconds(_CURVE, 2, kind=FailureKind.RATE_LIMIT, limit_scope=scope) == (
        120.0
    )


def test_a_concurrency_refusal_is_capped_by_the_unscaled_ceiling():
    """The scale multiplies the cap as well as the base delay, so leaving it at 4.0
    let a concurrency wait grow to 4x max_backoff_seconds (1h against a 15min
    ceiling) -- the tail of the 37-minute Tier 1 measurement."""
    assert (
        capacity_backoff_seconds(
            _CURVE,
            9,  # 30 * 2**7 = 3840s uncapped
            kind=FailureKind.RATE_LIMIT,
            limit_scope=LimitScope.REQUEST_CONCURRENCY,
        )
        == 900.0
    )
    assert (
        capacity_backoff_seconds(_CURVE, 9, kind=FailureKind.RATE_LIMIT, limit_scope=None) == 3600.0
    )


def test_a_non_capacity_failure_is_unaffected_by_the_scope_lookup():
    """A TOOL_ERROR carries no limit_scope and has no per-kind scale; the wrapper
    must be a pass-through for it, not a second opinion on the plan's curve."""
    assert capacity_backoff_seconds(
        _CURVE, 3, kind=FailureKind.TOOL_ERROR, limit_scope=None
    ) == _CURVE.backoff_for(3, kind=FailureKind.TOOL_ERROR)


# ── effective in-flight cap ───────────────────────────────────────────────────
#
# A non-positive override is not a limit an operator can have meant: `0` would
# refuse every attempt and a negative one refuses them harder, and neither opens
# a circuit or a block, so the plan waits forever with nothing to look at. The
# API refuses such a value on write; these rows can still exist from before that
# bound, so the resolver must not honour them either.


def test_a_model_override_wins_over_the_provider_and_the_default():
    assert resolve_max_inflight(model_cap=2, provider_cap=5, default=8) == 2


def test_a_provider_override_wins_over_the_default():
    assert resolve_max_inflight(model_cap=None, provider_cap=5, default=8) == 5


def test_the_default_applies_when_neither_row_declares_one():
    assert resolve_max_inflight(model_cap=None, provider_cap=None, default=8) == 8


@pytest.mark.parametrize("bad", [0, -1, -8])
def test_a_non_positive_model_override_falls_through_to_the_provider(bad):
    assert resolve_max_inflight(model_cap=bad, provider_cap=5, default=8) == 5


@pytest.mark.parametrize("bad", [0, -1, -8])
def test_a_non_positive_provider_override_falls_through_to_the_default(bad):
    """The wedge this exists to prevent: admission is `inflight >= cap`, so a
    stored `-1` declined every attempt with nothing in flight."""
    assert resolve_max_inflight(model_cap=None, provider_cap=bad, default=8) == 8


@pytest.mark.parametrize("bad", [0, -1])
def test_a_non_positive_default_falls_back_to_the_policy_default(bad):
    """Reached through `execution.provider_max_inflight`, whose stored value is a
    STRING — so the factory's `or` never saw a falsy `0` and passed it straight
    through."""
    assert resolve_max_inflight(model_cap=None, provider_cap=None, default=bad) == (
        ProviderCapacityPolicy().max_inflight
    )
