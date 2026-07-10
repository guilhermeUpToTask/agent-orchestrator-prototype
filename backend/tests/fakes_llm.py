"""FakeLLMClient — a scripted LLMClient for driving the agent loop and the
OpenAIReasoner without a provider. Pops one AssistantTurn per complete() call
and records every request (messages snapshot + tool names) for assertions."""
from __future__ import annotations

import json
from typing import Any

from src.infra.reasoner.runtime.llm_client import AssistantTurn
from src.infra.reasoner.runtime.tools import ToolCall, ToolSpec


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
