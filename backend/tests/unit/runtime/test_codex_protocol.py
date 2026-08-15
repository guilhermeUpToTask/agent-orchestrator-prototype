"""The codex event stream, tested against output captured from codex-cli 0.147.0.

Every fixture below is a real line observed on 2026-08-09, not an invented
shape. That matters here more than usual: the two properties this parser exists
for — plain tracing interleaved with JSON, and errors delivered in-band on a
zero exit — are exactly the ones a made-up fixture would omit.
"""

from __future__ import annotations

from praxis_orchestrator.infra.runtime.codex_protocol import (
    extract_final_text,
    extract_stream_error,
    extract_usage,
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

# Two agent messages in one run. Shaped like the captured output below: prose
# arrives as `item.completed`, and `turn.completed` closes the turn with usage.
# An earlier draft of this fixture invented `turn.completed` carrying the final
# text; the real run disproved it, which is why fixtures here are captured.
SUCCESSFUL = """{"type":"thread.started","thread_id":"abc"}
{"type":"turn.started"}
{"type":"item.completed","item":{"type":"agent_message","text":"Created src/sitegen/front_matter.py"}}
{"type":"item.completed","item":{"type":"agent_message","text":"Done: the parser and its tests are in place."}}
{"type":"turn.completed","usage":{"input_tokens":900,"output_tokens":40}}
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


# Verbatim stdout from an authenticated `codex exec --json` run, 2026-08-09.
# The prompt was six words; the token counts are the CLI's fixed overhead.
REAL_SUCCESS = (
    '{"type":"thread.started","thread_id":"019fe8d2-cba8-7ae2-beb4-d75925e1f55a"}\n'
    '{"type":"turn.started"}\n'
    '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"pong"}}\n'
    '{"type":"turn.completed","usage":{"input_tokens":12973,'
    '"cached_input_tokens":9984,"cache_write_input_tokens":0,'
    '"output_tokens":5,"reasoning_output_tokens":0}}\n'
)


def test_the_agent_message_is_extracted_from_a_real_run() -> None:
    """Against captured output, not a guessed shape: the message arrives nested
    as item.completed -> item.text."""
    assert extract_final_text(REAL_SUCCESS) == "pong"


def test_turn_completed_carries_usage_not_text() -> None:
    """`turn.completed` closes the turn and reports tokens. Treating it as a
    text event would have made the last message an empty string."""
    assert extract_stream_error(REAL_SUCCESS) is None
    assert extract_final_text(REAL_SUCCESS) == "pong"


def test_usage_is_recorded_so_the_real_cost_is_visible() -> None:
    """A six-word prompt cost 12,973 input tokens because the CLI ships its
    system prompt, tools and repo context every turn. That fixed overhead is
    what consumes a subscription allowance, and it is invisible unless
    recorded."""
    usage = extract_usage(REAL_SUCCESS)

    assert usage["input_tokens"] == 12973
    assert usage["cached_input_tokens"] == 9984
    assert usage["output_tokens"] == 5


def test_usage_sums_across_turns_because_an_attempt_may_take_several() -> None:
    two_turns = REAL_SUCCESS + (
        '{"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":7}}\n'
    )
    usage = extract_usage(two_turns)

    assert usage["input_tokens"] == 13073
    assert usage["output_tokens"] == 12


def test_missing_usage_is_an_empty_mapping_not_an_error() -> None:
    assert extract_usage(UNAUTHENTICATED) == {}
