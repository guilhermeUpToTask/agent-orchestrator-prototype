"""The tool-calling agent loop: terminal accept, {accepted:false}
self-correction, plain-reply semantics, budget exhaustion, malformed calls."""

from __future__ import annotations

import asyncio
import json

import pytest

from src.infra.reasoner.runtime.agent_loop import run_tool_session
from src.infra.reasoner.runtime.errors import ReasonerError
from src.infra.reasoner.runtime.tools import ToolSpec
from tests.fakes_llm import FakeLLMClient, text_turn, tool_turn


def submit_tool(handler=None, name="submit"):
    return ToolSpec(
        name=name,
        description="submit",
        input_schema={"type": "object"},
        handler=handler or (lambda args: json.dumps({"accepted": True})),
        terminal=True,
    )


def run(client, tools, **kw):
    messages = [{"role": "user", "content": "go"}]
    result = asyncio.run(run_tool_session(client, messages, tools, **kw))
    return result, messages


def test_terminal_accept_ends_session_with_args():
    client = FakeLLMClient([tool_turn("submit", {"payload": 42})])
    result, messages = run(client, [submit_tool()])

    assert result.submitted is True
    assert result.submit_args == {"payload": 42}
    assert result.turns == 1
    # transcript: user, assistant tool call, tool result
    assert [m["role"] for m in messages] == ["user", "assistant", "tool"]


def test_rejected_submit_feeds_errors_back_and_model_corrects():
    """The self-correction loop: first submit rejected with errors, the model
    sees them in a tool message and resubmits fixed."""
    attempts = []

    def handler(args):
        attempts.append(args)
        if args.get("n", 0) < 1:
            return json.dumps({"accepted": False, "errors": ["n must be >= 1"]})
        return json.dumps({"accepted": True})

    client = FakeLLMClient([tool_turn("submit", {"n": 0}), tool_turn("submit", {"n": 3}, "call-2")])
    result, messages = run(client, [submit_tool(handler)])

    assert result.submitted is True and result.submit_args == {"n": 3}
    assert attempts == [{"n": 0}, {"n": 3}]
    # the rejection was fed back verbatim before the second turn
    rejection = next(m for m in messages if m["role"] == "tool")
    assert "n must be >= 1" in rejection["content"]
    # the second model call saw the rejection in its context
    assert any(
        m.get("role") == "tool" and "n must be >= 1" in m.get("content", "")
        for m in client.calls[1]["messages"]
    )


def test_plain_text_is_the_reply_when_allowed():
    client = FakeLLMClient([text_turn("which database do you prefer?")])
    result, _ = run(client, [submit_tool()], allow_plain_reply=True)

    assert result.submitted is False
    assert result.text == "which database do you prefer?"


def test_plain_text_raises_transient_when_submit_required():
    client = FakeLLMClient([text_turn("here is my plan in prose...")])
    with pytest.raises(ReasonerError) as err:
        run(client, [submit_tool()], allow_plain_reply=False)
    assert err.value.transient is True


def test_budget_exhaustion_raises_transient():
    def rejecting(args):
        return json.dumps({"accepted": False, "errors": ["still wrong"]})

    client = FakeLLMClient([tool_turn("submit", {}, f"c{i}") for i in range(3)])
    with pytest.raises(ReasonerError) as err:
        run(client, [submit_tool(rejecting)], max_turns=3)
    assert err.value.transient is True


def test_unknown_tool_and_handler_crash_become_error_results():
    def exploding(args):
        raise ValueError("boom")

    client = FakeLLMClient(
        [
            tool_turn("nonexistent", {}, "c1"),
            tool_turn("fragile", {}, "c2"),
            tool_turn("submit", {}, "c3"),
        ]
    )
    fragile = ToolSpec(
        name="fragile",
        description="",
        input_schema={"type": "object"},
        handler=exploding,
    )
    result, messages = run(client, [submit_tool(), fragile], max_turns=5)

    assert result.submitted is True  # the loop survived both bad calls
    tool_messages = [m["content"] for m in messages if m["role"] == "tool"]
    assert any("Unknown tool: nonexistent" in c for c in tool_messages)
    assert any("boom" in c for c in tool_messages)


def test_non_terminal_tool_result_feeds_back_and_loop_continues():
    catalog = ToolSpec(
        name="lookup",
        description="",
        input_schema={"type": "object"},
        handler=lambda args: json.dumps({"found": ["a", "b"]}),
    )
    client = FakeLLMClient([tool_turn("lookup", {}, "c1"), tool_turn("submit", {}, "c2")])
    result, messages = run(client, [submit_tool(), catalog])

    assert result.submitted is True and result.turns == 2
    assert any(m["role"] == "tool" and "found" in m["content"] for m in messages)
