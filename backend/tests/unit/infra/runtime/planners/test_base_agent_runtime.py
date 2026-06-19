"""
tests/unit/infra/runtime/planners/test_base_agent_runtime.py

Regression tests for the provider-agnostic planner agent loop. These guard the
bug where the loop matched the terminal tool by a hardcoded name
(``submit_final_roadmap``) while the architecture session actually exposed
``submit_architecture`` — every real architecture run hit max turns and failed.

The loop must now:
  * recognize the terminal tool by its ``terminal=True`` flag, not its name;
  * stop raising on budget exhaustion when ``require_submit=False`` (the caller
    finalizes), while still reporting whether an explicit submit happened;
  * honor a cooperative ``cancel_check`` polled between turns.
"""
from __future__ import annotations

import json

import pytest

from src.domain.ports.planner import PlannerRuntimeError, PlannerTool
from src.infra.runtime.planners.base_agent_runtime import BasePlannerRuntime
from src.infra.runtime.planners.runtime_types import (
    NormalizedAssistantTurn,
    NormalizedToolCall,
)


class _ScriptedAdapter:
    """Replays a fixed list of assistant turns; records how many were sent."""

    def __init__(self, turns: list[NormalizedAssistantTurn]) -> None:
        self._turns = turns
        self.sent = 0

    def initial_messages(self, prompt: str) -> list[dict]:
        return [{"role": "user", "content": prompt}]

    def to_provider_tools(self, tools: list[PlannerTool]) -> list[dict]:
        return [{"name": t.name} for t in tools]

    def send_turn(self, messages, provider_tools) -> NormalizedAssistantTurn:
        turn = self._turns[min(self.sent, len(self._turns) - 1)]
        self.sent += 1
        return turn

    def append_assistant_turn(self, messages, turn) -> None:
        messages.append({"role": "assistant"})

    def append_tool_results(self, messages, results) -> None:
        messages.append({"role": "tool"})

    def extract_artifact(self, messages, submit_tool_name, artifact_arg) -> dict:
        return {}


def _tool_call(name: str, args: dict | None = None) -> NormalizedToolCall:
    return NormalizedToolCall(id=f"tc-{name}", name=name, arguments=args or {})


def _turn(tool_calls: list[NormalizedToolCall]) -> NormalizedAssistantTurn:
    return NormalizedAssistantTurn(
        content_blocks=[{"type": "text", "text": "..."}],
        tool_calls=tool_calls,
        final_text="done",
        reasoning="",
        provider_message={},
    )


def _accepting_tool(name: str, terminal: bool) -> PlannerTool:
    return PlannerTool(
        name=name,
        description=name,
        input_schema={"type": "object", "properties": {}},
        handler=lambda _inp: json.dumps({"accepted": True}),
        terminal=terminal,
    )


class TestTerminalDetection:
    def test_terminal_tool_recognized_by_flag_not_name(self):
        """A differently-named terminal tool still ends the session."""
        tools = [_accepting_tool("submit_architecture", terminal=True)]
        adapter = _ScriptedAdapter([_turn([_tool_call("submit_architecture")])])
        # submit_tool_name intentionally mismatches the actual tool name.
        runtime = BasePlannerRuntime(adapter, submit_tool_name="submit_final_roadmap")

        out = runtime.run_session(prompt="p", tools=tools, max_turns=5)

        assert out.submitted is True
        assert adapter.sent == 1  # broke immediately after the submit

    def test_non_terminal_tool_does_not_submit(self):
        tools = [_accepting_tool("propose_decision", terminal=False)]
        # Agent keeps calling a non-terminal tool; loop should exhaust the budget.
        adapter = _ScriptedAdapter([_turn([_tool_call("propose_decision")])])
        runtime = BasePlannerRuntime(adapter)

        out = runtime.run_session(
            prompt="p", tools=tools, max_turns=3, require_submit=False
        )

        assert out.submitted is False
        assert adapter.sent == 3  # ran the whole budget without submitting


class TestRequireSubmit:
    def test_budget_exhaustion_raises_when_require_submit(self):
        tools = [_accepting_tool("propose_decision", terminal=False)]
        adapter = _ScriptedAdapter([_turn([_tool_call("propose_decision")])])
        runtime = BasePlannerRuntime(adapter)

        with pytest.raises(PlannerRuntimeError):
            runtime.run_session(
                prompt="p", tools=tools, max_turns=2, require_submit=True
            )

    def test_budget_exhaustion_returns_when_not_require_submit(self):
        tools = [_accepting_tool("propose_decision", terminal=False)]
        adapter = _ScriptedAdapter([_turn([_tool_call("propose_decision")])])
        runtime = BasePlannerRuntime(adapter)

        out = runtime.run_session(
            prompt="p", tools=tools, max_turns=2, require_submit=False
        )
        assert out.submitted is False


class TestCancelCheck:
    def test_cancel_check_stops_loop_before_any_turn(self):
        tools = [_accepting_tool("submit_architecture", terminal=True)]
        adapter = _ScriptedAdapter([_turn([_tool_call("submit_architecture")])])
        runtime = BasePlannerRuntime(adapter)

        out = runtime.run_session(
            prompt="p",
            tools=tools,
            max_turns=5,
            require_submit=False,
            cancel_check=lambda: True,
        )

        assert out.submitted is False
        assert adapter.sent == 0  # cancelled before the first send

    def test_cancel_after_first_turn(self):
        calls = {"n": 0}

        def cancel() -> bool:
            calls["n"] += 1
            return calls["n"] > 1  # allow the first turn, cancel before the second

        tools = [_accepting_tool("propose_decision", terminal=False)]
        adapter = _ScriptedAdapter([_turn([_tool_call("propose_decision")])])
        runtime = BasePlannerRuntime(adapter)

        out = runtime.run_session(
            prompt="p",
            tools=tools,
            max_turns=10,
            require_submit=False,
            cancel_check=cancel,
        )

        assert out.submitted is False
        assert adapter.sent == 1  # only one turn ran before cancel fired
