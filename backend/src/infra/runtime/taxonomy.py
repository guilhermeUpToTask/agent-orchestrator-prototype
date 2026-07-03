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

from src.domain.value_objects.lifecycle import FailureKind

_PATTERNS: list[tuple[FailureKind, re.Pattern[str]]] = [
    (
        FailureKind.RATE_LIMIT,
        re.compile(r"rate.?limit|too many requests|\b429\b|overloaded", re.I),
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


def classify_failure(output: str, *, timed_out: bool = False) -> FailureKind:
    if timed_out:
        return FailureKind.TIMEOUT
    for kind, pattern in _PATTERNS:
        if pattern.search(output):
            return kind
    return FailureKind.TOOL_ERROR
