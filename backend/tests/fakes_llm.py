"""FakeLLMClient — a scripted LLMClient for driving the agent loop and the
OpenAIReasoner without a provider. Pops one AssistantTurn per complete() call
and records every request (messages snapshot + tool names) for assertions."""

from __future__ import annotations

import json
from typing import Any

from agent_orchestrator.infra.reasoner.runtime.llm_client import AssistantTurn
from agent_orchestrator.infra.reasoner.runtime.tools import ToolCall, ToolSpec


def text_turn(text: str, usage: dict[str, int] | None = None) -> AssistantTurn:
    return AssistantTurn(
        text=text,
        tool_calls=[],
        raw_message={"role": "assistant", "content": text},
        usage=usage,
    )


def tool_turn(
    name: str,
    arguments: dict[str, Any],
    call_id: str = "call-1",
    usage: dict[str, int] | None = None,
) -> AssistantTurn:
    return AssistantTurn(
        text="",
        tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
        raw_message={
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            ],
        },
        usage=usage,
    )


class FakeLLMClient:
    def __init__(self, script: list[AssistantTurn]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> AssistantTurn:
        self.calls.append(
            {
                "messages": [dict(m) for m in messages],
                "tool_names": [t.name for t in tools],
            }
        )
        if not self.script:
            raise AssertionError("FakeLLMClient script exhausted")
        return self.script.pop(0)


class GreedyReaderLLMClient:
    """A model that KEEPS READING and never volunteers a submission.

    `FakeLLMClient` replays a fixed script, so it cannot *choose* to keep
    reading — which is precisely why no test could fail on the 2026-08-09
    starvation defect, where `converse` and `architect_cycle` offered readers
    against one submission tool and reserved no turns for it. A read-happy model
    burned the whole budget and submitted nothing; the scripted fake was
    physically unable to reproduce that.

    This one reacts to what it is OFFERED: it calls a read tool for as long as
    one is available, and submits only when the loop has WITHDRAWN the readers
    and left it no other option. If the reserve is ever removed, a test using
    this client hangs onto reads until the budget dies — exactly the production
    symptom.
    """

    def __init__(self, submit_arguments: dict[str, Any], reader_name: str | None = None) -> None:
        self._submit_arguments = submit_arguments
        self._reader_name = reader_name
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> AssistantTurn:
        names = [t.name for t in tools]
        self.calls.append(
            {"messages": [dict(m) for m in messages], "tool_names": list(names)}
        )
        readers = [
            n
            for n in names
            if not n.startswith("submit_")
            and (self._reader_name is None or n == self._reader_name)
        ]
        if readers:
            return tool_turn(readers[0], {}, call_id=f"read-{len(self.calls)}")
        submissions = [n for n in names if n.startswith("submit_")]
        if not submissions:
            raise AssertionError(f"no tool this client can call was offered: {names}")
        return tool_turn(
            submissions[0], self._submit_arguments, call_id=f"submit-{len(self.calls)}"
        )


class SilentLLMClient:
    """A provider that returns an empty completion: no text, no tool calls.

    Observed for real on 2026-08-09 (`content: []`, all-zero token usage). The
    scripted fake could only emit shapes a test author thought to write, so this
    path — the one that makes a run look like a successful no-op — had no
    coverage at all.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> AssistantTurn:
        self.calls.append(
            {"messages": [dict(m) for m in messages], "tool_names": [t.name for t in tools]}
        )
        return AssistantTurn(
            text="",
            tool_calls=[],
            raw_message={"role": "assistant", "content": None},
            usage={"prompt_tokens": 0, "completion_tokens": 0},
        )
