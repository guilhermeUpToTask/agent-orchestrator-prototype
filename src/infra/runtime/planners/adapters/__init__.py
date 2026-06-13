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
    text = str(exc).lower()
    if status_code == 404 or "tool use" in text or "tool_use" in text:
        message = (
            f"The configured model '{model}' does not support tool use, which the "
            "planner requires. Select a tool-capable model/provider."
        )
    else:
        message = f"Planner LLM request failed ({type(exc).__name__}): {exc}"
    return PlannerRuntimeError(message)
