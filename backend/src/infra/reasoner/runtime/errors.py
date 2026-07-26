"""
Reasoner runtime errors + provider-error classification.

`transient` marks a failure worth retrying (provider timeout, rate limit,
upstream blip) as opposed to a permanent config error (a model that does not
support tool use). The classification inspects duck-typed attributes and
strings, never provider exception classes — provider SDK imports stay in
llm_client.py.
"""

from __future__ import annotations

import re

from src.app.ports import ReasonerUnavailable
from src.domain.value_objects.lifecycle import FailureKind
from src.infra.errors import InfrastructureError
from src.infra.runtime.taxonomy import classify_failure, parse_retry_after_seconds


class ReasonerError(InfrastructureError, ReasonerUnavailable):
    """The planning LLM runtime could not produce a usable turn/artifact.

    Subclasses the app-layer ReasonerUnavailable so the PlanningHandler can catch
    it without importing infra, AND InfrastructureError so the API error map keys
    off `code` (REASONER_FAILED) on the chat path — one exception, both roles."""

    code = "REASONER_FAILED"

    def __init__(
        self,
        message: str,
        *,
        transient: bool = False,
        kind: FailureKind | None = None,
        retry_after_seconds: float | None = None,
        turns_used: int | None = None,
    ) -> None:
        super().__init__(message)  # InfrastructureError.__init__ (MRO)
        self.reason = message
        self.transient = transient
        self.kind = kind
        self.retry_after_seconds = retry_after_seconds
        self.turns_used = turns_used
        self.partial_artifact = None
        self.rejection_reasons = ()
        self.input_fingerprint = None


# Rate-limit-adjacent wording that classify_failure's RATE_LIMIT pattern does
# not itself cover (it matches "rate limit"/"429"/"resource exhausted" text,
# but not a bare "quota" or "credit" mention, which providers also use for
# capacity rejections).
_RATE_LIMIT_EXTRA_WORDS = re.compile(r"quota|credit", re.IGNORECASE)


def classify_provider_error(model: str, exc: Exception) -> ReasonerError:
    """Translate a raw provider API error into an actionable ReasonerError.

    A tool-use rejection — the provider has no endpoint that supports tool use,
    surfaced by OpenRouter as a ``404`` — becomes a message that names the model
    and the requirement, so the session fails with operator-actionable text
    instead of an opaque raw provider string. Anything else is wrapped
    generically but still cleanly.
    """
    status_code = getattr(exc, "status_code", None)
    exc_name = type(exc).__name__
    text = str(exc).lower()
    # Transient by default: timeouts and generic provider/network failures are
    # worth a retry. A tool-use rejection is a permanent config error.
    transient = True
    if exc_name == "APITimeoutError" or "timed out" in text or "timeout" in text:
        message = (
            f"Reasoner LLM request to model '{model}' timed out. The model may be "
            "slow, overloaded, or unreachable — retry, or pick a faster model."
        )
        kind = FailureKind.TIMEOUT
    elif status_code == 404 or "tool use" in text or "tool_use" in text:
        message = (
            f"The configured model '{model}' does not support tool use, which the "
            "reasoner requires. Select a tool-capable model/provider."
        )
        transient = False
        kind = FailureKind.TOOL_ERROR
    else:
        message = f"Reasoner LLM request failed ({exc_name}): {exc}"
        kind = FailureKind.RATE_LIMIT if status_code == 429 else FailureKind.CONNECTION_ERROR
    return ReasonerError(
        message,
        transient=transient,
        kind=kind,
        retry_after_seconds=parse_retry_after_seconds(text),
    )


def provider_error_from_empty_choices(
    model: str, response: object, *, degenerate_choice: bool = False
) -> ReasonerError:
    """Build a ReasonerError for a 200 response that carries no usable answer.

    Some OpenAI-compatible providers (OpenRouter and similar proxies) return an
    error inside an HTTP 200 body instead of a non-2xx status. The OpenAI SDK
    parses that body into a completion with ``choices=None`` and an extra
    ``error`` field, so it never raises ``openai.APIError`` and the runtime
    would otherwise crash indexing ``None``. This turns it into an actionable
    (transient) failure.
    """
    detail = _extract_provider_error_text(response)
    symptom = (
        "returned a choice with neither content nor tool calls"
        if degenerate_choice
        else "returned no choices"
    )
    message = (
        f"Reasoner LLM request to model '{model}' {symptom}: {detail}. "
        "The provider rejected the request (e.g. out of credits, rate limited, "
        "or upstream error)."
    )
    names_rate_limit = (
        classify_failure(detail) == FailureKind.RATE_LIMIT
        or _RATE_LIMIT_EXTRA_WORDS.search(detail) is not None
    )
    kind = FailureKind.RATE_LIMIT if names_rate_limit else FailureKind.CONNECTION_ERROR
    return ReasonerError(
        message,
        transient=True,
        kind=kind,
        retry_after_seconds=parse_retry_after_seconds(detail),
    )


def _extract_provider_error_text(response: object) -> str:
    """Pull a human-readable error string from an in-band provider error.

    Handles both dict-shaped and object-shaped ``error`` payloads, and falls
    back to a truncated dump of the whole response when no ``error`` is present.
    """
    error = getattr(response, "error", None)
    if error is not None:
        if isinstance(error, dict):
            message = error.get("message")
            code = error.get("code")
        else:
            message = getattr(error, "message", None)
            code = getattr(error, "code", None)
        if message:
            return f"{message}" + (f" (code={code})" if code is not None else "")
        return str(error)

    dump = getattr(response, "model_dump", None)
    raw = str(dump()) if callable(dump) else str(response)
    return raw[:500]
