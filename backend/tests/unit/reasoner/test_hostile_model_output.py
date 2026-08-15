"""The tool loop against output no well-behaved model produces.

Phase 10A sweep 3. The package already refuses to trust provider *schema*
enforcement (`_validate_submission` re-validates every submission). These lock
the other half: what the loop does with a turn that is hostile or simply broken
in shape rather than in content.

Two properties, both of which were unbounded or leaky before:

  1. a turn's tool-call fan-out is capped, and every call is still answered;
  2. an unexpected handler exception does not travel to the provider.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from praxis_orchestrator.infra.reasoner.runtime.agent_loop import (
    _MAX_TOOL_CALLS_PER_TURN,
    run_tool_session,
)
from praxis_orchestrator.infra.reasoner.runtime.llm_client import AssistantTurn
from praxis_orchestrator.infra.reasoner.runtime.tools import ToolCall, ToolSpec
from tests.fakes_llm import FakeLLMClient, tool_turn


def _submit_tool(handler=None) -> ToolSpec:
    return ToolSpec(
        name="submit",
        description="submit",
        input_schema={"type": "object"},
        handler=handler or (lambda args: json.dumps({"accepted": True})),
        terminal=True,
    )


def _read_tool(handler) -> ToolSpec:
    return ToolSpec(
        name="read",
        description="read",
        input_schema={"type": "object"},
        handler=handler,
    )


def _fan_out_turn(count: int) -> AssistantTurn:
    """One assistant turn carrying `count` tool calls."""
    return AssistantTurn(
        text="",
        tool_calls=[
            ToolCall(id=f"c{i}", name="read", arguments={"i": i}) for i in range(count)
        ],
        raw_message={"role": "assistant", "content": None, "tool_calls": []},
        usage=None,
    )


def _run(client, tools, **kw):
    messages = [{"role": "user", "content": "go"}]
    result = asyncio.run(run_tool_session(client, messages, tools, **kw))
    return result, messages


def test_a_huge_fan_out_runs_at_most_the_cap() -> None:
    """The regression: 500 calls in one turn ran 500 handlers, each of which
    reaches the repository reader."""
    invocations = []
    client = FakeLLMClient([_fan_out_turn(500), tool_turn("submit", {"ok": 1})])

    result, _ = _run(
        client,
        [_read_tool(lambda a: invocations.append(a) or json.dumps({"ok": True})), _submit_tool()],
        max_turns=4,
    )

    assert len(invocations) == _MAX_TOOL_CALLS_PER_TURN
    assert result.submitted is True


def test_every_call_is_answered_even_when_the_cap_is_hit() -> None:
    """Protocol, not politeness: providers require one tool message per
    `tool_call_id` in the assistant message, so the excess must be REFUSED
    rather than dropped — a silent drop malforms the next request."""
    count = _MAX_TOOL_CALLS_PER_TURN + 9
    client = FakeLLMClient([_fan_out_turn(count), tool_turn("submit", {"ok": 1})])

    _, messages = _run(
        client, [_read_tool(lambda a: json.dumps({"ok": True})), _submit_tool()], max_turns=4
    )

    answered = [m for m in messages if m.get("role") == "tool"]
    fan_out_answers = answered[:count]
    assert len(fan_out_answers) == count
    assert {m["tool_call_id"] for m in fan_out_answers} == {f"c{i}" for i in range(count)}

    refusals = [m for m in fan_out_answers if "Not executed" in m["content"]]
    assert len(refusals) == count - _MAX_TOOL_CALLS_PER_TURN


def test_a_normal_fan_out_is_untouched() -> None:
    """The cap must not interfere with a model making a few parallel reads."""
    invocations = []
    client = FakeLLMClient([_fan_out_turn(3), tool_turn("submit", {"ok": 1})])

    _run(
        client,
        [_read_tool(lambda a: invocations.append(a) or json.dumps({"ok": True})), _submit_tool()],
        max_turns=4,
    )

    assert len(invocations) == 3


INTERNAL_DETAIL = "/home/dev/.orchestrator/secrets.db row 7"


def _exploding(_args):
    raise RuntimeError(f"could not open {INTERNAL_DETAIL}")


def test_a_handler_crash_does_not_travel_to_the_provider() -> None:
    """The regression: `except Exception -> {"error": str(exc)}` put the raw
    message in a tool result, which the NEXT request carries upstream."""
    client = FakeLLMClient([tool_turn("read", {}), tool_turn("submit", {"ok": 1})])

    result, messages = _run(client, [_read_tool(_exploding), _submit_tool()], max_turns=4)

    assert result.submitted is True  # the loop still survives a broken tool
    transcript = json.dumps(messages)
    assert INTERNAL_DETAIL not in transcript
    assert "secrets.db" not in transcript
    # And specifically not in what was actually SENT on the second round-trip.
    second_request = json.dumps(client.calls[1]["messages"])
    assert INTERNAL_DETAIL not in second_request


def test_the_model_is_still_told_the_tool_failed() -> None:
    """Redaction must not leave the model guessing — it has to know the call
    failed, or it cannot adapt."""
    client = FakeLLMClient([tool_turn("read", {}), tool_turn("submit", {"ok": 1})])

    _, messages = _run(client, [_read_tool(_exploding), _submit_tool()], max_turns=4)

    failure = json.loads(next(m for m in messages if m.get("role") == "tool")["content"])
    assert "read" in failure["error"]
    assert "failed" in failure["error"]


def test_an_unknown_tool_is_still_named_back_to_the_model() -> None:
    """An unknown NAME is the model's own mistake and carries no internal
    detail, so it stays specific."""
    client = FakeLLMClient([tool_turn("not_a_tool", {}), tool_turn("submit", {"ok": 1})])

    _, messages = _run(client, [_submit_tool()], max_turns=4)

    failure = json.loads(next(m for m in messages if m.get("role") == "tool")["content"])
    assert failure == {"error": "Unknown tool: not_a_tool"}


def test_two_terminal_calls_in_one_turn_accept_the_first_deterministically() -> None:
    client = FakeLLMClient(
        [
            AssistantTurn(
                text="",
                tool_calls=[
                    ToolCall(id="c1", name="submit", arguments={"which": "first"}),
                    ToolCall(id="c2", name="submit", arguments={"which": "second"}),
                ],
                raw_message={"role": "assistant", "content": None},
                usage=None,
            )
        ]
    )

    result, _ = _run(client, [_submit_tool()], max_turns=2)

    assert result.submit_args == {"which": "first"}


@pytest.mark.parametrize("arguments", [{}, {"unexpected": None}])
def test_a_terminal_call_with_empty_or_junk_args_is_the_handlers_decision(
    arguments,
) -> None:
    """The loop must not second-guess the handler: a rejection is
    `{"accepted": false}`, and the session continues to the turn budget."""
    rejecting = _submit_tool(lambda a: json.dumps({"accepted": False, "errors": ["no"]}))
    client = FakeLLMClient([tool_turn("submit", arguments), tool_turn("submit", arguments)])

    with pytest.raises(Exception) as caught:
        _run(client, [rejecting], max_turns=2)

    assert "without submitting" in str(caught.value)
