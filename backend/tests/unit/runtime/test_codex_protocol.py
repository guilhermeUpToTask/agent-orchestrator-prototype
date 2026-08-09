"""The codex event stream, tested against output captured from codex-cli 0.147.0.

Every fixture below is a real line observed on 2026-08-09, not an invented
shape. That matters here more than usual: the two properties this parser exists
for — plain tracing interleaved with JSON, and errors delivered in-band on a
zero exit — are exactly the ones a made-up fixture would omit.
"""

from __future__ import annotations

from agent_orchestrator.infra.runtime.codex_protocol import (
    extract_final_text,
    extract_stream_error,
    parse_codex_events,
)

# Verbatim from an unauthenticated `codex exec --json` run.
UNAUTHENTICATED = """WARNING: proceeding, even though we could not create PATH aliases
Reading additional input from stdin...
{"type":"thread.started","thread_id":"019fe8bf-3d6c-7ee3-85a9-f8f57dac6670"}
{"type":"turn.started"}
2026-08-09T22:57:56.697777Z ERROR codex_api::endpoint::responses_websocket: failed to connect
{"type":"error","message":"Reconnecting... 2/5 (unexpected status 401 Unauthorized)"}
2026-08-09T22:57:58.132883Z ERROR codex_api::endpoint::responses_websocket: failed to connect
{"type":"error","message":"Reconnecting... 5/5 (unexpected status 401 Unauthorized)"}
"""

SUCCESSFUL = """{"type":"thread.started","thread_id":"abc"}
{"type":"turn.started"}
{"type":"item.completed","item":{"text":"Created src/sitegen/front_matter.py"}}
{"type":"turn.completed","text":"Done: the parser and its tests are in place."}
"""


def test_plain_tracing_between_json_lines_does_not_break_parsing() -> None:
    """The CLI writes human-readable logs on the same descriptor. A parser that
    assumes every line is JSON throws the whole run away."""
    events = parse_codex_events(UNAUTHENTICATED)

    assert [event_type for event_type, _ in events] == [
        "thread.started",
        "turn.started",
        "error",
        "error",
    ]


def test_an_in_band_error_is_surfaced_for_classification() -> None:
    """codex exits 0 while reporting an auth failure inside the stream. Left
    undetected the run looks like a successful no-op, and a later stage
    mislabels it — a test-authoring run surfaces as the terminal 'produced no
    executable checks' instead of the real cause."""
    error = extract_stream_error(UNAUTHENTICATED)

    assert error is not None
    assert "401 Unauthorized" in error


def test_the_last_error_wins_because_retries_each_emit_one() -> None:
    """codex retries internally (5 attempts observed) and reports each as its
    own error event. The final one is the outcome; the earlier ones are noise."""
    assert "5/5" in (extract_stream_error(UNAUTHENTICATED) or "")


def test_a_clean_run_carries_no_stream_error() -> None:
    assert extract_stream_error(SUCCESSFUL) is None


def test_the_final_message_is_the_task_output_not_the_raw_stream() -> None:
    assert extract_final_text(SUCCESSFUL) == "Done: the parser and its tests are in place."


def test_text_nested_under_item_is_found() -> None:
    stream = '{"type":"item.completed","item":{"text":"only nested text"}}\n'
    assert extract_final_text(stream) == "only nested text"


def test_empty_output_yields_nothing_rather_than_raising() -> None:
    assert extract_final_text("") is None
    assert extract_stream_error("") is None
    assert parse_codex_events("") == []
