"""
src/api/logging/config.py — structured logging + mandatory secret masking.

Single logger configuration for the API process. Two cross-cutting guarantees:

  * every log record is enriched with the current ``request_id`` (contextvar);
  * sensitive values are redacted before rendering — plaintext secrets never
    reach a log record. This reuses the secret discipline: ``SecretStr`` reprs
    are already masked, and a processor redacts known sensitive field names.

Set ``LOG_JSON=0`` for human-readable console logs in dev (default is JSON).
"""
from __future__ import annotations

import logging
import os
from typing import Any, MutableMapping

import structlog
from pydantic import SecretStr

from src.api.middleware.request_logging import get_request_id

# Field names whose values must never be logged in clear text.
_SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "password", "passwd", "secret", "token", "api_key", "apikey",
        "authorization", "auth", "cookie", "session", "access_token",
        "refresh_token", "github_token", "anthropic_api_key", "openai_api_key",
        "gemini_api_key", "openrouter_api_key", "master_key", "plaintext",
        "ciphertext", "wrapped_key",
    }
)
_MASK = "***"


def _mask_value(value: Any) -> Any:
    if isinstance(value, SecretStr):
        return _MASK
    if isinstance(value, dict):
        return {k: (_MASK if _is_sensitive(k) else _mask_value(v)) for k, v in value.items()}
    return value


def _is_sensitive(key: str) -> bool:
    return key.lower() in _SENSITIVE_KEYS


def secret_masking_processor(
    _logger: Any, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor: redact sensitive keys + SecretStr values."""
    for key in list(event_dict.keys()):
        if _is_sensitive(key):
            event_dict[key] = _MASK
        else:
            event_dict[key] = _mask_value(event_dict[key])
    return event_dict


def add_request_id(
    _logger: Any, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor: stamp every record with the current request_id."""
    event_dict.setdefault("request_id", get_request_id())
    return event_dict


def configure_logging() -> None:
    """Configure structlog for the API process (idempotent)."""
    json_output = os.environ.get("LOG_JSON", "1") != "0"
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            add_request_id,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            secret_masking_processor,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )
