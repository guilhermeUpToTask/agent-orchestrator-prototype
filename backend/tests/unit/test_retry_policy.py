from __future__ import annotations

from src.domain.policies.retry_policies import RetryPolicy
from src.domain.value_objects.lifecycle import FailureKind


def test_per_kind_attempt_budgets() -> None:
    policy = RetryPolicy()

    assert policy.should_retry(5, FailureKind.RATE_LIMIT)
    assert not policy.should_retry(6, FailureKind.RATE_LIMIT)
    assert policy.should_retry(4, FailureKind.CONNECTION_ERROR)
    assert not policy.should_retry(5, FailureKind.CONNECTION_ERROR)
    assert policy.should_retry(2, FailureKind.TOOL_ERROR)
    assert not policy.should_retry(3, FailureKind.TOOL_ERROR)


def test_rate_limit_backoff_scales_initial_and_cap() -> None:
    policy = RetryPolicy(
        initial_backoff_seconds=10,
        backoff_multiplier=2,
        max_backoff_seconds=15,
        jitter_ratio=0,
    )

    assert policy.backoff_for(2, kind=FailureKind.RATE_LIMIT) == 40.0
    assert policy.backoff_for(3, kind=FailureKind.RATE_LIMIT) == 60.0


def test_none_kind_keeps_the_unscaled_backoff_curve() -> None:
    policy = RetryPolicy(
        initial_backoff_seconds=10,
        backoff_multiplier=2,
        max_backoff_seconds=15,
        jitter_ratio=0,
    )
    unscaled_policy = policy.model_copy(update={"kind_backoff_scale": {}})

    for attempt in range(1, 6):
        assert policy.backoff_for(attempt, kind=None) == unscaled_policy.backoff_for(attempt)


def test_operator_raised_max_attempts_is_never_shadowed_by_a_kind_budget() -> None:
    """The `execution.retry_max_attempts` config key (#47) exists so an operator
    can ride out a provider capacity outage on automatic backoff alone instead of
    opening a human-gated block. The per-kind budgets (#49) grant transient kinds
    EXTRA attempts -- they must never cut the configured baseline back down, or
    raising the key silently does nothing for the exact failure kind it was
    written for."""
    policy = RetryPolicy(max_attempts=10)

    assert policy.should_retry(6, FailureKind.RATE_LIMIT)
    assert policy.should_retry(9, FailureKind.RATE_LIMIT)
    assert not policy.should_retry(10, FailureKind.RATE_LIMIT)
    assert policy.should_retry(9, FailureKind.CONNECTION_ERROR)
    # A kind with no entry already tracked max_attempts; keep it that way.
    assert policy.should_retry(9, FailureKind.TOOL_ERROR)
    assert not policy.should_retry(10, FailureKind.TOOL_ERROR)
    # Terminal kinds stay terminal no matter how large the budget is.
    assert not policy.should_retry(0, FailureKind.AUTH_ERROR)


def test_kind_budget_still_extends_beyond_a_low_max_attempts() -> None:
    """The other direction still holds: a default (low) max_attempts must still
    be extended by the per-kind budget."""
    policy = RetryPolicy(max_attempts=3)

    assert policy.should_retry(5, FailureKind.RATE_LIMIT)
    assert not policy.should_retry(6, FailureKind.RATE_LIMIT)
    assert not policy.should_retry(3, FailureKind.TOOL_ERROR)


def test_old_shape_rehydrates_with_per_kind_defaults() -> None:
    policy = RetryPolicy.model_validate(
        {
            "max_attempts": 3,
            "initial_backoff_seconds": 30.0,
            "backoff_multiplier": 2.0,
            "max_backoff_seconds": 900.0,
            "jitter_ratio": 0.2,
            "non_retryable_kinds": [
                FailureKind.TOKEN_LIMIT,
                FailureKind.AUTH_ERROR,
                FailureKind.VERIFICATION_ERROR,
            ],
        }
    )

    assert policy.kind_max_attempts == {
        FailureKind.RATE_LIMIT: 6,
        FailureKind.CONNECTION_ERROR: 5,
    }
    assert policy.kind_backoff_scale == {FailureKind.RATE_LIMIT: 4.0}


def test_a_rejected_candidate_gets_a_second_attempt_but_not_a_third() -> None:
    """VERIFICATION_ERROR was terminal on the FIRST failure, so one bad agent
    output blocked the goal — observed live, where attempt 2 failed
    `test author produced no executable checks` and a human was required.

    Agent output is a sample. Re-running it against the same frozen tests, now
    told exactly what was rejected, is the cheapest recovery there is. But it is
    capped low on purpose: unlike a rate limit, a third identical rejection is
    evidence the CONTRACT is wrong, not that the attempt was unlucky, and
    retrying cannot fix a contract.
    """
    policy = RetryPolicy()

    assert policy.should_retry(1, FailureKind.VERIFICATION_ERROR)
    assert not policy.should_retry(2, FailureKind.VERIFICATION_ERROR)


def test_a_kind_ceiling_is_not_raised_by_a_generous_operator_budget() -> None:
    """`kind_max_attempts` is a FLOOR — it grants a transient kind extra tries
    and deliberately cannot be cut by a lower global budget. A ceiling is the
    opposite instrument and must survive a higher one, or an operator who raises
    max_attempts for rate limits silently pays for ten identical verification
    failures."""
    policy = RetryPolicy(max_attempts=10)

    assert policy.should_retry(5, FailureKind.RATE_LIMIT)  # floor still grants
    assert not policy.should_retry(2, FailureKind.VERIFICATION_ERROR)  # ceiling still caps


def test_token_limit_and_auth_stay_terminal_on_the_first_failure() -> None:
    """These fail identically on every retry; only VERIFICATION_ERROR moved."""
    policy = RetryPolicy()

    assert not policy.should_retry(0, FailureKind.TOKEN_LIMIT)
    assert not policy.should_retry(0, FailureKind.AUTH_ERROR)


def test_a_capacity_wait_does_not_spend_the_verification_ceiling() -> None:
    """Observed live: a task waited out three provider rate limits, then its
    FIRST real candidate rejection blocked the goal immediately.

    `cycle_attempt` counts every attempt, including ones that never reached the
    work — a capacity failure means the provider had no room, not that the agent
    produced something wrong. Counting those against a ceiling of 2 means any
    task unlucky enough to hit an outage gets ZERO verification retries, which
    is the exact behaviour un-freeze #17 set out to remove.

    The ceiling must therefore be spent by attempts of the SAME kind.
    """
    policy = RetryPolicy()

    # three capacity failures already recorded; this is the first rejection
    assert policy.should_retry(0, FailureKind.VERIFICATION_ERROR)
    assert policy.should_retry(1, FailureKind.VERIFICATION_ERROR)
    assert not policy.should_retry(2, FailureKind.VERIFICATION_ERROR)


def test_kinds_without_a_ceiling_are_unaffected() -> None:
    policy = RetryPolicy()

    assert policy.kind_attempt_ceiling.get(FailureKind.RATE_LIMIT) is None
    assert policy.should_retry(5, FailureKind.RATE_LIMIT)
