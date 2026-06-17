"""src/infra/runtime/planners/adapters/__init__.py — shared adapter helpers.

Provider SDK imports stay inside their own adapter modules; this package
only hosts provider-agnostic helpers (it inspects duck-typed attributes and
strings, never provider exception classes).
"""
from __future__ import annotations

from src.domain.ports.planner import PlannerRuntimeError


def classify_provider_error(model: str, exc: Exception) -> PlannerRuntimeError:
    """Translate a raw provider API error into an actionable PlannerRuntimeError.

    A tool-use rejection — the provider has no endpoint that supports tool use,
    surfaced by OpenRouter as a ``404`` — becomes a message that names the model
    and the requirement, so the planner session fails with operator-actionable
    text instead of an opaque raw provider string. Anything else is wrapped
    generically but still cleanly, so the runtime treats it as a normal session
    failure rather than letting it propagate as a 500.
    """
    status_code = getattr(exc, "status_code", None)
    exc_name = type(exc).__name__
    text = str(exc).lower()
    # Transient by default: timeouts and generic provider/network failures are
    # worth a retry. A tool-use rejection is a permanent config error.
    transient = True
    if exc_name == "APITimeoutError" or "timed out" in text or "timeout" in text:
        message = (
            f"Planner LLM request to model '{model}' timed out. The model may be "
            "slow, overloaded, or unreachable — retry, or pick a faster model."
        )
    elif status_code == 404 or "tool use" in text or "tool_use" in text:
        message = (
            f"The configured model '{model}' does not support tool use, which the "
            "planner requires. Select a tool-capable model/provider."
        )
        transient = False
    else:
        message = f"Planner LLM request failed ({exc_name}): {exc}"
    return PlannerRuntimeError(message, transient=transient)


def provider_error_from_empty_choices(model: str, response: object) -> PlannerRuntimeError:
    """Build a PlannerRuntimeError for a 200 response that carries no choices.

    Some OpenAI-compatible providers (OpenRouter and similar proxies) return an
    error inside an HTTP 200 body instead of a non-2xx status. The OpenAI SDK
    parses that body into a completion with ``choices=None`` and an extra
    ``error`` field, so it never raises ``openai.APIError`` and the runtime would
    otherwise crash indexing ``None``. This turns it into an actionable failure.
    """
    detail = _extract_provider_error_text(response)
    message = (
        f"Planner LLM request to model '{model}' returned no choices: {detail}. "
        "The provider rejected the request (e.g. out of credits, rate limited, "
        "or upstream error)."
    )
    return PlannerRuntimeError(message, transient=True)


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
