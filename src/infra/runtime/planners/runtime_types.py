from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NormalizedToolCall:
    id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class NormalizedAssistantTurn:
    content_blocks: list[dict]
    tool_calls: list[NormalizedToolCall]
    final_text: str
    reasoning: str
    provider_message: dict


@dataclass(frozen=True)
class NormalizedToolResult:
    tool_call_id: str
    tool_name: str
    result_str: str
