"""
src/infra/runtime/pi_protocol.py — the pi stdio contract, isolated (roadmap 2.4).

PiAgentRunner runs pi in `--mode json`, whose stdout is an NDJSON stream of
session events (message deltas, tool executions, usage). Only an allowlisted
event vocabulary is promoted to agent events; unknown/malformed lines remain
ordinary bounded output. Raw prompts, message deltas, and credential-shaped
fields are never copied into events.
"""

from __future__ import annotations

import json
from typing import Any

_ALLOWED_TYPES = {"tool.started", "tool.finished", "step", "model.usage"}
_ALLOWED_FIELDS = {
    "name",
    "status",
    "elapsed_seconds",
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "cached_tokens",
    "total_tokens",
    "model",
    "provider",
}

# pi `--mode json` usage field -> the allowlisted agent-event field.
_USAGE_FIELDS = {
    "input": "input_tokens",
    "output": "output_tokens",
    "reasoning": "reasoning_tokens",
    "cacheRead": "cached_tokens",
    "totalTokens": "total_tokens",
}


def _iter_records(output: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in output.splitlines():
        try:
            value = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def _usage_event(message: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None
    payload: dict[str, Any] = {
        target: usage[source]
        for source, target in _USAGE_FIELDS.items()
        if isinstance(usage.get(source), (int, float))
    }
    for key in ("model", "provider"):
        if isinstance(message.get(key), str):
            payload[key] = message[key]
    return ("model.usage", payload) if payload else None


def parse_pi_events(output: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for value in _iter_records(output):
        kind = value.get("type")
        if kind in _ALLOWED_TYPES:
            payload_raw = value.get("payload", value)
            if not isinstance(payload_raw, dict):
                payload_raw = {}
            payload = {key: payload_raw[key] for key in _ALLOWED_FIELDS if key in payload_raw}
            events.append((str(kind), payload))
        elif kind == "tool_execution_start":
            events.append(("tool.started", {"name": str(value.get("toolName") or "")}))
        elif kind == "tool_execution_end":
            events.append(
                (
                    "tool.finished",
                    {
                        "name": str(value.get("toolName") or ""),
                        "status": "error" if value.get("isError") else "ok",
                    },
                )
            )
        elif kind == "message_end":
            message = value.get("message")
            if isinstance(message, dict) and message.get("role") == "assistant":
                usage = _usage_event(message)
                if usage is not None:
                    events.append(usage)
    return events


def extract_final_text(output: str) -> str | None:
    """The last assistant message's text content, or None when no NDJSON matched.

    This is the human-readable outcome of a `--mode json` run; the raw NDJSON
    stream stays in the bounded runtime log for the live feed.
    """
    final: str | None = None
    for value in _iter_records(output):
        if value.get("type") != "message_end":
            continue
        message = value.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        texts = [
            item["text"]
            for item in content
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)
        ]
        if texts:
            final = "\n".join(texts)
    return final
