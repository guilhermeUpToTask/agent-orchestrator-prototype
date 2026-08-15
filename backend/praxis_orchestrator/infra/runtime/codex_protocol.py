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

# Event types carrying the agent's final prose. Confirmed against a real run:
# the message arrives as `{"type":"item.completed","item":{"type":
# "agent_message","text":"…"}}`. `turn.completed` is deliberately NOT here — it
# closes the turn and carries `usage`, not text.
_TEXT_EVENT_TYPES = frozenset({"item.completed", "thread.message"})

# Token accounting, reported once per turn on `turn.completed`. Worth capturing
# rather than discarding: a trivial six-word prompt measured 12,973 input
# tokens (9,984 of them cached), because the CLI ships its system prompt, tool
# definitions and repository context every turn. That fixed overhead — not the
# task text — is what actually consumes a subscription's allowance, and it is
# invisible unless recorded.
_USAGE_EVENT_TYPES = frozenset({"turn.completed"})


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


def extract_usage(output: str) -> dict[str, int]:
    """Summed token usage across every turn in the run, or {} when absent.

    Summed rather than last-wins: one orchestrator attempt can contain several
    turns, and the cost of the attempt is their total. Only integer fields are
    kept, so a future field of another type cannot corrupt the accounting.
    """
    totals: dict[str, int] = {}
    for value in _iter_records(output):
        if value.get("type") not in _USAGE_EVENT_TYPES:
            continue
        usage = value.get("usage")
        if not isinstance(usage, dict):
            continue
        for key, raw in usage.items():
            if isinstance(raw, bool) or not isinstance(raw, int):
                continue
            totals[key] = totals.get(key, 0) + raw
    return totals


def parse_codex_events(output: str) -> list[tuple[str, dict[str, Any]]]:
    """(type, payload) for the live agent feed. Empty when nothing parsed."""
    return [
        (str(value["type"]), value)
        for value in _iter_records(output)
        if isinstance(value.get("type"), str)
    ]
