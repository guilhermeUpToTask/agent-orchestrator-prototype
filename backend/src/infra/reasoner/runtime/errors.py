"""
Reasoner runtime errors + provider-error classification.

`transient` marks a failure worth retrying (provider timeout, rate limit,
upstream blip) as opposed to a permanent config error (a model that does not
support tool use). The classification inspects duck-typed attributes and
strings, never provider exception classes — provider SDK imports stay in
llm_client.py.
"""

from __future__ import annotations

from src.app.ports import ReasonerUnavailable
from src.infra.errors import InfrastructureError


class ReasonerError(InfrastructureError, ReasonerUnavailable):
    """The planning LLM runtime could not produce a usable turn/artifact.

    Subclasses the app-layer ReasonerUnavailable so the PlanningHandler can catch
    it without importing infra, AND InfrastructureError so the API error map keys
    off `code` (REASONER_FAILED) on the chat path — one exception, both roles."""

    code = "REASONER_FAILED"

    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)  # InfrastructureError.__init__ (MRO)
        self.reason = message
        self.transient = transient


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
    elif status_code == 404 or "tool use" in text or "tool_use" in text:
        message = (
            f"The configured model '{model}' does not support tool use, which the "
            "reasoner requires. Select a tool-capable model/provider."
        )
        transient = False
    else:
        message = f"Reasoner LLM request failed ({exc_name}): {exc}"
    return ReasonerError(message, transient=transient)


def provider_error_from_empty_choices(model: str, response: object) -> ReasonerError:
    """Build a ReasonerError for a 200 response that carries no choices.

    Some OpenAI-compatible providers (OpenRouter and similar proxies) return an
    error inside an HTTP 200 body instead of a non-2xx status. The OpenAI SDK
    parses that body into a completion with ``choices=None`` and an extra
    ``error`` field, so it never raises ``openai.APIError`` and the runtime
    would otherwise crash indexing ``None``. This turns it into an actionable
    (transient) failure.
    """
    detail = _extract_provider_error_text(response)
    message = (
        f"Reasoner LLM request to model '{model}' returned no choices: {detail}. "
        "The provider rejected the request (e.g. out of credits, rate limited, "
        "or upstream error)."
    )
    return ReasonerError(message, transient=True)


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
