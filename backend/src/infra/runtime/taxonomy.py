"""
src/infra/runtime/taxonomy.py — subprocess outcome -> the SHARED FAILURE TAXONOMY.

One constant classification used by the real CLI runners; the dummy runner emits
the same FailureKind values directly — so dummy-driven tests exercise exactly
the retry/terminal paths production hits (roadmap 2.4 #12).

Classification is pattern-matching over the process output. It is deliberately
conservative: anything unrecognized is TOOL_ERROR (retryable) rather than a
guessed terminal kind — misclassifying a transient failure as terminal kills a
plan; the reverse merely wastes a retry.
"""

from __future__ import annotations

import re

from src.app.runtime_failures import LimitScope, RuntimeFailure, safe_runtime_tail
from src.domain.value_objects.lifecycle import FailureKind

_PATTERNS: list[tuple[FailureKind, re.Pattern[str]]] = [
    (
        FailureKind.RATE_LIMIT,
        re.compile(
            r"rate.?limit|request limit reached|too many requests|\b429\b"
            r"|overloaded|resource[_ ]?exhausted|capacity exhausted",
            re.I,
        ),
    ),
    (
        FailureKind.AUTH_ERROR,
        re.compile(
            r"invalid (api )?key|unauthorized|authentication|permission denied"
            r"|forbidden|\b401\b|\b403\b",
            re.I,
        ),
    ),
    (
        FailureKind.TOKEN_LIMIT,
        re.compile(
            r"context.?(length|window)|token limit|max.?tokens|prompt is too long",
            re.I,
        ),
    ),
    (
        FailureKind.CONNECTION_ERROR,
        re.compile(
            r"connection|econnrefused|econnreset|enotfound|getaddrinfo|network"
            r"|socket hang up|dns",
            re.I,
        ),
    ),
]

_RETRY_AFTER = re.compile(
    r"(?:retry[- ]?after|retry in|try again in)\D{0,12}(\d+(?:\.\d+)?)\s*(ms|s|sec(?:onds?)?|m|min(?:utes?)?)?",
    re.I,
)
_PROVIDER_CODE = re.compile(
    r"\b(RESOURCE_EXHAUSTED|ResourceExhausted|RATE_LIMITED|QUOTA_EXCEEDED|429)\b"
)


def _retry_after_seconds(output: str) -> float | None:
    match = _RETRY_AFTER.search(output)
    if match is None:
        return None
    value = float(match.group(1))
    unit = (match.group(2) or "s").lower()
    if unit == "ms":
        return value / 1000.0
    if unit.startswith("m"):
        return value * 60.0
    return value


def _limit_scope(output: str) -> LimitScope:
    lowered = output.lower()
    if "per day" in lowered or "daily" in lowered or "free-models-per-day" in lowered:
        return LimitScope.DAILY_QUOTA
    if "concurr" in lowered or "simultaneous" in lowered:
        return LimitScope.REQUEST_CONCURRENCY
    if "quota" in lowered or "credit" in lowered:
        return LimitScope.QUOTA
    return LimitScope.UNKNOWN_CAPACITY


def classify_failure(output: str, *, timed_out: bool = False) -> FailureKind:
    if timed_out:
        return FailureKind.TIMEOUT
    for kind, pattern in _PATTERNS:
        if pattern.search(output):
            return kind
    return FailureKind.TOOL_ERROR


def normalize_failure(
    *,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
    runtime: str | None = None,
    provider_id: str | None = None,
    model_id: str | None = None,
    exit_code: int | None = None,
) -> RuntimeFailure:
    output = f"{stdout}\n{stderr}"
    kind = classify_failure(output, timed_out=timed_out)
    non_retryable = {
        FailureKind.AUTH_ERROR,
        FailureKind.TOKEN_LIMIT,
        FailureKind.VERIFICATION_ERROR,
    }
    provider_code = _PROVIDER_CODE.search(output)
    normalized_provider_code = None
    if provider_code is not None:
        raw_provider_code = provider_code.group(1)
        normalized_provider_code = (
            "RESOURCE_EXHAUSTED"
            if raw_provider_code.lower() == "resourceexhausted"
            else raw_provider_code.upper()
        )
    message = safe_runtime_tail(
        stderr or stdout or ("runtime timed out" if timed_out else "runtime failed")
    )
    return RuntimeFailure(
        kind=kind,
        safe_message=message,
        retryable=kind not in non_retryable,
        runtime=runtime,
        provider_id=provider_id,
        model_id=model_id,
        provider_code=normalized_provider_code,
        retry_after_seconds=_retry_after_seconds(output),
        limit_scope=(_limit_scope(output) if kind == FailureKind.RATE_LIMIT else None),
        exit_code=exit_code,
        stdout_tail=safe_runtime_tail(stdout),
        stderr_tail=safe_runtime_tail(stderr),
    )
