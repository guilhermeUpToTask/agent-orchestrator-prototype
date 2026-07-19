"""
src/infra/runtime/pi_protocol.py — the pi stdio contract, isolated (roadmap 2.4).

PiAgentRunner uses one-shot mode, but builds that emit NDJSON are parsed
into an allowlisted event vocabulary. Unknown/malformed lines remain ordinary
bounded output; raw prompts and credential-shaped fields are never copied.
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


def parse_pi_events(output: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for line in output.splitlines():
        try:
            value = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict) or value.get("type") not in _ALLOWED_TYPES:
            continue
        payload_raw = value.get("payload", value)
        if not isinstance(payload_raw, dict):
            payload_raw = {}
        payload = {key: payload_raw[key] for key in _ALLOWED_FIELDS if key in payload_raw}
        events.append((str(value["type"]), payload))
    return events
