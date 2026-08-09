"""Parse the `codex exec --json` event stream.

Observed empirically against codex-cli 0.147.0 on 2026-08-09 rather than
assumed, because two properties of this stream are not guessable:

* **It is not pure JSONL.** The CLI interleaves human-readable tracing on the
  same descriptor (`2026-…Z ERROR codex_api::endpoint…`). A parser that assumes
  every line is JSON throws away the run.
* **Errors arrive in-band while the process still exits 0**, exactly like pi's
  errored assistant turn: `{"type":"error","message":"…401 Unauthorized…"}`.
  Left undetected, an auth failure or a rate limit looks like a successful
  empty run, and a later stage mislabels it — a test-authoring run surfaces as
  the terminal "produced no executable checks" instead of the retryable (or
  terminal-but-honest) cause it actually was.

Codex also retries internally before giving up (5 attempts observed on a 401).
That is worth knowing when reading timings: one orchestrator attempt can
already contain several provider round-trips.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

# Event types carrying the agent's final prose. `item.completed` wraps a
# structured item; `turn.completed` closes a turn.
_TEXT_EVENT_TYPES = frozenset({"item.completed", "turn.completed", "thread.message"})


def _iter_records(output: str) -> Iterator[dict[str, Any]]:
    """Every JSON object on its own line, skipping the CLI's plain tracing."""
    for line in output.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            yield value


def extract_stream_error(output: str) -> str | None:
    """The last in-band error message, or None when the run carried none.

    Returns the LAST one because codex reports each internal retry as its own
    error event ("Reconnecting… 2/5", "3/5", …); the final one is the outcome
    and the earlier ones are noise.
    """
    error: str | None = None
    for value in _iter_records(output):
        if value.get("type") != "error":
            continue
        message = value.get("message")
        if isinstance(message, str) and message.strip():
            error = message.strip()
    return error


def extract_final_text(output: str) -> str | None:
    """The agent's last textual message, never the raw event stream."""
    text: str | None = None
    for value in _iter_records(output):
        if value.get("type") not in _TEXT_EVENT_TYPES:
            continue
        for candidate in _candidate_texts(value):
            if candidate:
                text = candidate
    return text


def _candidate_texts(value: dict[str, Any]) -> Iterator[str]:
    """Text carried directly, or nested one level under `item`/`message`."""
    for key in ("text", "content", "message"):
        raw = value.get(key)
        if isinstance(raw, str) and raw.strip():
            yield raw.strip()
    for key in ("item", "message"):
        nested = value.get(key)
        if isinstance(nested, dict):
            for inner in ("text", "content"):
                raw = nested.get(inner)
                if isinstance(raw, str) and raw.strip():
                    yield raw.strip()


def parse_codex_events(output: str) -> list[tuple[str, dict[str, Any]]]:
    """(type, payload) for the live agent feed. Empty when nothing parsed."""
    return [
        (str(value["type"]), value)
        for value in _iter_records(output)
        if isinstance(value.get("type"), str)
    ]
