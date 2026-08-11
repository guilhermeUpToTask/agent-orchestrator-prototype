"""The tool-calling agent loop: terminal accept, {accepted:false}
self-correction, plain-reply semantics, budget exhaustion, malformed calls."""

from __future__ import annotations

import asyncio
import json

import pytest

from agent_orchestrator.domain.value_objects.lifecycle import FailureKind
from agent_orchestrator.infra.reasoner.runtime.agent_loop import run_tool_session
from agent_orchestrator.infra.reasoner.runtime.errors import ReasonerError
from agent_orchestrator.infra.reasoner.runtime.tools import ToolSpec
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


def test_plain_text_is_nudged_once_before_the_session_is_abandoned():
    """Observed live on nvidia/nemotron-3-super-120b-a12b:free: after six read
    turns the model answered with EMPTY text instead of calling the only tool
    left, and the whole session was thrown away — six turns of provider spend,
    a transient failure, backoff, and a retry that started from nothing.

    A model that forgets to call a tool is the cheapest possible failure to
    correct: say so and let it try again inside the same session.
    """
    client = FakeLLMClient(
        [
            text_turn("here is my plan in prose..."),
            tool_turn("submit", {"v": 1}, "s1"),
        ]
    )
    result, messages = run(client, [submit_tool()], allow_plain_reply=False, max_turns=4)

    assert result.submitted is True and result.submit_args == {"v": 1}
    nudges = [
        m["content"]
        for m in messages
        if m.get("role") == "user" and "requires a tool call" in str(m.get("content", ""))
    ]
    assert len(nudges) == 1 and "submit" in nudges[0]
    # the correction is visible to the model on its next turn
    assert any("submit" in str(m.get("content", "")) for m in client.calls[1]["messages"])


def test_a_json_payload_emitted_as_prose_is_quoted_back_to_be_resent_as_a_tool_call():
    """Observed live on openai/gpt-oss-20b:free: after reading the repository the
    model produced a well-formed contract inside a ```json fence — as TEXT, not
    as a tool call. The payload was right; only the transport was wrong.

    Quoting it back makes compliance trivial and costs no regeneration. It is
    deliberately NOT accepted as a submission: the payload still has to arrive
    through the tool call, because re-validating tool arguments is the boundary
    the whole design rests on.
    """
    payload = '```json\n{"objective": "ship it", "tasks": [{"n": 1}]}\n```'
    client = FakeLLMClient([text_turn(payload), tool_turn("submit", {"objective": "ship it"}, "s1")])

    result, messages = run(client, [submit_tool()], allow_plain_reply=False, max_turns=4)

    assert result.submitted is True
    nudge = next(
        m["content"] for m in messages if "requires a tool call" in str(m.get("content", ""))
    )
    assert '"objective": "ship it"' in nudge  # their own payload, handed back
    assert "already produced" in nudge


def test_prose_is_nudged_more_than_once_before_giving_up():
    """One lapse is common; two in a row still does not justify throwing away
    every read turn the session paid for."""
    client = FakeLLMClient(
        [
            text_turn("prose one"),
            text_turn("prose two"),
            tool_turn("submit", {"v": 1}, "s1"),
        ]
    )

    result, messages = run(client, [submit_tool()], allow_plain_reply=False, max_turns=6)

    assert result.submitted is True
    nudges = [m for m in messages if "requires a tool call" in str(m.get("content", ""))]
    assert len(nudges) == 2


def test_a_model_that_will_not_call_a_tool_still_fails_transiently():
    """The nudge is bounded. A model that cannot call tools at all — a real
    configuration error — must still surface, not loop to the turn ceiling."""
    client = FakeLLMClient([text_turn("prose"), text_turn("more prose"), text_turn("still prose")])

    with pytest.raises(ReasonerError) as err:
        run(client, [submit_tool()], allow_plain_reply=False, max_turns=5)

    assert err.value.transient is True
    assert err.value.kind is FailureKind.TOOL_ERROR


def test_budget_exhaustion_raises_transient():
    def rejecting(args):
        return json.dumps({"accepted": False, "errors": ["still wrong"]})

    client = FakeLLMClient([tool_turn("submit", {}, f"c{i}") for i in range(3)])
    with pytest.raises(ReasonerError) as err:
        run(client, [submit_tool(rejecting)], max_turns=3)
    assert err.value.transient is True
    assert err.value.kind is FailureKind.TOOL_ERROR


def read_tool(name="lookup"):
    return ToolSpec(
        name=name,
        description="",
        input_schema={"type": "object"},
        handler=lambda args: json.dumps({"found": True}),
    )


def test_reads_cannot_starve_the_reserved_submit_turns():
    """Observed live: enrichment offers six read tools on a 4-turn budget, the model
    spent every turn reading, and the session died `exceeded 4 turns without
    submitting` — never once attempting a submission. Worse, the submit handler's
    rejections are built on the model getting a repair turn, which a read-starved
    budget cannot promise.

    With turns reserved for submission the readers are WITHDRAWN from the request
    once the read budget is spent, so the remaining turns are arithmetically
    guaranteed to the terminal tool instead of merely hoped for.
    """
    rejections = []

    def handler(args):
        rejections.append(args)
        if len(rejections) < 2:
            return json.dumps({"accepted": False, "errors": ["fix it"]})
        return json.dumps({"accepted": True})

    client = FakeLLMClient(
        [
            tool_turn("lookup", {}, "r1"),
            tool_turn("lookup", {}, "r2"),
            tool_turn("submit", {"v": 1}, "s1"),  # rejected
            tool_turn("submit", {"v": 2}, "s2"),  # repaired
        ]
    )
    result, _ = run(
        client,
        [submit_tool(handler), read_tool()],
        max_turns=4,
        reserved_submit_turns=2,
    )

    assert result.submitted is True and result.submit_args == {"v": 2}
    # reads offered while the read budget lasted, withdrawn once it was spent
    assert client.calls[0]["tool_names"] == ["submit", "lookup"]
    assert client.calls[1]["tool_names"] == ["submit", "lookup"]
    assert client.calls[2]["tool_names"] == ["submit"]
    assert client.calls[3]["tool_names"] == ["submit"]


def test_reserved_turns_are_not_consumed_by_a_model_that_only_reads():
    """A model that never stops reading still reaches the terminal tool: the
    reserve is withheld from reads, not merely counted after them."""
    client = FakeLLMClient([tool_turn("lookup", {}, f"r{i}") for i in range(3)])
    client.script.append(tool_turn("submit", {"v": 9}, "s1"))

    result, _ = run(
        client,
        [submit_tool(), read_tool()],
        max_turns=4,
        reserved_submit_turns=1,
    )

    assert result.submitted is True
    assert [c["tool_names"] for c in client.calls][-1] == ["submit"]


def test_no_reserve_keeps_the_previous_all_tools_every_turn_behavior():
    """Conversation passes no reserve and must be unaffected."""
    client = FakeLLMClient([tool_turn("lookup", {}, "r1"), tool_turn("submit", {}, "s1")])
    run(client, [submit_tool(), read_tool()], max_turns=2)

    assert all(c["tool_names"] == ["submit", "lookup"] for c in client.calls)


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
    # An unknown NAME is the model's own mistake and carries no internal detail,
    # so it is named back precisely.
    assert any("Unknown tool: nonexistent" in c for c in tool_messages)
    # A handler CRASH is reported without its exception text: `str(exc)` on an
    # unexpected error is internal detail and these messages are sent to the
    # provider (Phase 10A — see test_hostile_model_output.py). The model is told
    # which tool failed, which is what it needs to adapt.
    assert any("fragile" in c and "failed" in c for c in tool_messages)
    assert not any("boom" in c for c in tool_messages)


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
