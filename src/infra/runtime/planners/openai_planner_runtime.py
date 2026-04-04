"""
src/infra/runtime/openai_planner_runtime.py — OpenAI-spec Planner runtime.

Works with any OpenAI-compatible endpoint (OpenAI, vLLM, LM Studio, OpenRouter).
"""

import json
import logging
from typing import Callable, Optional

from openai import OpenAI

from src.domain.ports.planner import (
    PlannerOutput,
    PlannerRuntimeError,
    PlannerRuntimePort,
    PlannerTool,
)

log = logging.getLogger(__name__)


class OpenAIPlannerRuntime(PlannerRuntimePort):
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        base_url: Optional[str] = None,  # Allows pointing to local/alternative endpoints
    ) -> None:
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    def run_session(
        self,
        prompt: str,
        tools: list[PlannerTool],
        max_turns: int = 15,
        session_callback: Optional[Callable[[str, list[dict]], None]] = None,
    ) -> PlannerOutput:

        api_tools = [_tool_to_openai_api(t) for t in tools]
        tool_map = {t.name: t.handler for t in tools}

        # OpenAI uses a system prompt for behavior, user prompt for input
        messages = [
            {
                "role": "system",
                "content": "You are an expert software architect and planner. Use the provided tools to construct a roadmap.",
            },
            {"role": "user", "content": prompt},
        ]

        roadmap_accepted = False
        final_text = ""
        reasoning = ""  # Standard OpenAI models don't expose reasoning tokens directly, but we prep the variable.

        for turn in range(max_turns):
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                tools=api_tools,
                temperature=0.2,  # Lower temperature for planning tasks
            )

            msg = response.choices[0].message
            final_text = msg.content or ""

            # If using an o1/o3 model that exposes reasoning_content via API extension
            if hasattr(msg, "reasoning_content") and msg.reasoning_content:
                reasoning = msg.reasoning_content

            # Serialize and fire callback (adapted to your expected format)
            if session_callback:
                session_callback(
                    "assistant",
                    [{"text": final_text, "tool_calls": _serialize_tool_calls(msg.tool_calls)}],
                )

            # Append the assistant's message to history (OpenAI requires the exact message object dict)
            messages.append(msg.model_dump(exclude_none=True))

            # Process tool calls
            if not msg.tool_calls:
                break  # Model finished naturally

            for tool_call in msg.tool_calls:
                handler = tool_map.get(tool_call.function.name)

                try:
                    args = json.loads(tool_call.function.arguments)
                    if handler is None:
                        result_str = json.dumps(
                            {"error": f"Unknown tool: {tool_call.function.name}"}
                        )
                    else:
                        result_str = handler(args)
                except Exception as exc:
                    result_str = json.dumps({"error": str(exc)})

                # Check for exit condition
                if tool_call.function.name == "submit_final_roadmap":
                    try:
                        parsed = json.loads(result_str)
                        if parsed.get("accepted"):
                            roadmap_accepted = True
                    except Exception:
                        pass

                # OpenAI expects a specific 'tool' role message for each call
                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_str,
                }
                messages.append(tool_msg)

                if session_callback:
                    session_callback("tool", [tool_msg])

            if roadmap_accepted:
                break

        if not roadmap_accepted:
            raise PlannerRuntimeError(
                "Planning session exceeded max turns without submitting a roadmap"
            )

        roadmap_raw = _extract_roadmap_from_openai_history(messages)

        return PlannerOutput(
            reasoning=reasoning,
            roadmap_raw=roadmap_raw,
            raw_text=final_text,
            turns=messages,
        )


# --- Helpers ---


def _tool_to_openai_api(tool: PlannerTool) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }


def _serialize_tool_calls(tool_calls) -> list[dict]:
    if not tool_calls:
        return []
    return [tc.model_dump() for tc in tool_calls]


def _extract_roadmap_from_openai_history(messages: list[dict]) -> dict:
    """Find the roadmap_json from the tool call arguments in history."""
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg.get("tool_calls", []):
                if tc.get("function", {}).get("name") == "submit_final_roadmap":
                    try:
                        return json.loads(tc["function"]["arguments"]).get("roadmap_json", {})
                    except Exception:
                        pass
    return {}
