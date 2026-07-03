"""
Tool specs + dispatch for the reasoner agent loop (the old PlannerTool shape).

A handler takes the parsed tool arguments and returns a STRING result that is
fed back to the model. Terminal tools end the session when their handler
returns ``{"accepted": true, ...}``; returning ``{"accepted": false,
"errors": [...]}`` instead feeds the errors back for self-correction. Unknown
tools and handler exceptions become ``{"error": ...}`` results — the model
sees its mistake and corrects; the loop never crashes on a bad call.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel


class ToolCall(BaseModel):
    """One normalized tool invocation from an assistant turn."""

    id: str
    name: str
    arguments: dict[str, Any]


class ToolResult(BaseModel):
    tool_call_id: str
    tool_name: str
    result_str: str


@dataclass(frozen=True)
class ToolSpec:
    """A tool the reasoner agent can invoke during its loop."""

    name: str
    description: str
    input_schema: dict[str, Any]  # JSON Schema
    handler: Callable[[dict[str, Any]], str]  # parsed args -> string result
    terminal: bool = False  # accepted=True from this tool ends the session


def execute_tool_call(tools: list[ToolSpec], tool_call: ToolCall) -> ToolResult:
    handler = next((t.handler for t in tools if t.name == tool_call.name), None)

    if handler is None:
        result_str = json.dumps({"error": f"Unknown tool: {tool_call.name}"})
    else:
        try:
            result_str = handler(tool_call.arguments)
        except Exception as exc:
            result_str = json.dumps({"error": str(exc)})

    return ToolResult(
        tool_call_id=tool_call.id,
        tool_name=tool_call.name,
        result_str=result_str,
    )
