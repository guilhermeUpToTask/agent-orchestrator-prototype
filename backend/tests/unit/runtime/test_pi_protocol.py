"""pi --mode json NDJSON parsing: allowlisted events + final-text extraction."""

from __future__ import annotations

import json

from src.infra.runtime.pi_protocol import extract_final_text, parse_pi_events


def _assistant_message_end(texts: list[str], usage: dict | None = None) -> str:
    message: dict = {
        "role": "assistant",
        "content": [{"type": "thinking", "thinking": "secret reasoning"}]
        + [{"type": "text", "text": text} for text in texts],
        "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
        "provider": "openrouter",
    }
    if usage is not None:
        message["usage"] = usage
    return json.dumps({"type": "message_end", "message": message})


def test_v3_session_events_map_to_allowlisted_vocabulary():
    output = "\n".join(
        [
            '{"type":"agent_start"}',
            '{"type":"tool_execution_start","toolCallId":"c1","toolName":"write","args":{"path":"x"}}',
            '{"type":"tool_execution_end","toolCallId":"c1","toolName":"write","isError":false}',
            _assistant_message_end(
                ["Done."],
                usage={
                    "input": 1690,
                    "output": 28,
                    "reasoning": 18,
                    "cacheRead": 0,
                    "totalTokens": 1718,
                    "cost": {"total": 0},
                },
            ),
            "not json",
        ]
    )

    events = parse_pi_events(output)

    assert events == [
        ("tool.started", {"name": "write"}),
        ("tool.finished", {"name": "write", "status": "ok"}),
        (
            "model.usage",
            {
                "input_tokens": 1690,
                "output_tokens": 28,
                "reasoning_tokens": 18,
                "cached_tokens": 0,
                "total_tokens": 1718,
                "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
                "provider": "openrouter",
            },
        ),
    ]


def test_tool_error_and_non_assistant_messages():
    output = "\n".join(
        [
            '{"type":"tool_execution_end","toolName":"bash","isError":true}',
            '{"type":"message_end","message":{"role":"user","content":[{"type":"text","text":"prompt text"}]}}',
            '{"type":"message_end","message":{"role":"toolResult","content":[{"type":"text","text":"tool out"}]}}',
        ]
    )

    events = parse_pi_events(output)

    assert events == [("tool.finished", {"name": "bash", "status": "error"})]
    assert extract_final_text(output) is None


def test_legacy_allowlisted_types_still_parse_and_drop_prompt_fields():
    events = parse_pi_events(
        '{"type":"model.usage","payload":{"total_tokens":7,'
        '"input_tokens":5,"prompt":"private","api_key":"secret"}}'
    )
    assert events == [("model.usage", {"input_tokens": 5, "total_tokens": 7})]


def test_extract_final_text_takes_last_assistant_message():
    output = "\n".join(
        [
            _assistant_message_end(["First answer."]),
            '{"type":"turn_start"}',
            _assistant_message_end(["Final answer.", "Second line."]),
        ]
    )
    assert extract_final_text(output) == "Final answer.\nSecond line."
    assert "secret reasoning" not in (extract_final_text(output) or "")


def test_extract_final_text_none_for_plain_text_output():
    assert extract_final_text("plain one-shot pi output\n") is None
