from __future__ import annotations

import json

from src.domain.ports.planner import PlannerTool
from src.infra.runtime.planners.runtime_types import NormalizedToolCall, NormalizedToolResult


def execute_tool_call(tools: list[PlannerTool], tool_call: NormalizedToolCall) -> NormalizedToolResult:
    tool_map = {t.name: t.handler for t in tools}
    handler = tool_map.get(tool_call.name)

    if handler is None:
        result_str = json.dumps({"error": f"Unknown tool: {tool_call.name}"})
    else:
        try:
            result_str = handler(tool_call.arguments)
        except Exception as exc:
            result_str = json.dumps({"error": str(exc)})

    return NormalizedToolResult(
        tool_call_id=tool_call.id,
        tool_name=tool_call.name,
        result_str=result_str,
    )
