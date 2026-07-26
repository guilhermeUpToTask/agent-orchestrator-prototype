"""`_limit_scope` provider-wording coverage: NVIDIA's concurrency-cap message
(relayed through OpenRouter) must classify as REQUEST_CONCURRENCY, not fall
through to UNKNOWN_CAPACITY, while OpenRouter daily-cap wording must still win
over any concurrency-shaped substrings (daily checks run first)."""

from __future__ import annotations

from src.app.runtime_failures import LimitScope
from src.domain.value_objects.lifecycle import FailureKind
from src.infra.runtime.taxonomy import classify_failure, normalize_failure

_NVIDIA_CONCURRENCY_MESSAGE = (
    "Upstream error from Nvidia: ResourceExhausted: "
    "Worker local total request limit reached (33/32)"
)


def test_nvidia_concurrency_message_classifies_as_request_concurrency() -> None:
    failure = normalize_failure(stderr=_NVIDIA_CONCURRENCY_MESSAGE)
    assert failure.limit_scope == LimitScope.REQUEST_CONCURRENCY


def test_nvidia_concurrency_message_is_still_rate_limit_kind() -> None:
    # Regression guard: classification of the FailureKind itself must not
    # change when limit_scope wording is added/adjusted.
    assert classify_failure(_NVIDIA_CONCURRENCY_MESSAGE) == FailureKind.RATE_LIMIT


def test_openrouter_daily_message_stays_daily_quota_not_concurrency() -> None:
    failure = normalize_failure(
        stderr="Rate limit exceeded: free-models-per-day limit reached"
    )
    assert failure.limit_scope == LimitScope.DAILY_QUOTA


def test_request_limit_reached_phrasing_classifies_as_request_concurrency() -> None:
    failure = normalize_failure(stderr="429: request limit reached")
    assert failure.limit_scope == LimitScope.REQUEST_CONCURRENCY


def test_total_request_limit_phrasing_classifies_as_request_concurrency() -> None:
    failure = normalize_failure(stderr="rate limit: total request limit exceeded")
    assert failure.limit_scope == LimitScope.REQUEST_CONCURRENCY


def test_too_many_requests_phrasing_classifies_as_request_concurrency() -> None:
    failure = normalize_failure(stderr="rate limit: too many requests")
    assert failure.limit_scope == LimitScope.REQUEST_CONCURRENCY


def test_generic_rate_limit_with_no_scope_wording_is_unknown_capacity() -> None:
    failure = normalize_failure(stderr="429 rate limit exceeded")
    assert failure.limit_scope == LimitScope.UNKNOWN_CAPACITY
