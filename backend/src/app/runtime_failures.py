"""Normalized, provider-neutral runtime failure evidence.

The object is safe to persist and return from operational APIs: messages and
process output are bounded, while prompts, source content, environment values,
credentials, and absolute workspace paths are deliberately absent.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import re

from src.domain.value_objects.lifecycle import FailureKind

_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|authorization|access[_-]?token|secret|password)"
    r"\s*[:=]\s*([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_KEY_SHAPE = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")


def safe_runtime_tail(output: str, limit: int = 2_000) -> str:
    """Return a bounded, single-line runtime excerpt with common secrets removed."""
    # Redact the complete bearer token before assignment redaction can consume
    # only the word "Bearer" and leave its credential behind.
    redacted = _BEARER.sub("Bearer [REDACTED]", output)
    redacted = _SENSITIVE_ASSIGNMENT.sub(r"\1=[REDACTED]", redacted)
    redacted = _KEY_SHAPE.sub("[REDACTED_API_KEY]", redacted)
    clean = " ".join(redacted.replace("\x00", "").split())
    return clean[-limit:]


class LimitScope(str, Enum):
    REQUEST_CONCURRENCY = "request_concurrency"
    QUOTA = "quota"
    DAILY_QUOTA = "daily_quota"
    UNKNOWN_CAPACITY = "unknown_capacity"


@dataclass(frozen=True)
class RuntimeFailure:
    kind: FailureKind
    safe_message: str
    retryable: bool
    runtime: str | None = None
    provider_id: str | None = None
    model_id: str | None = None
    provider_code: str | None = None
    retry_after_seconds: float | None = None
    limit_scope: LimitScope | None = None
    exit_code: int | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""

    def with_identity(
        self,
        *,
        runtime: str | None,
        provider_id: str | None,
        model_id: str | None,
    ) -> "RuntimeFailure":
        return replace(
            self,
            runtime=runtime or self.runtime,
            provider_id=provider_id or self.provider_id,
            model_id=model_id or self.model_id,
        )
