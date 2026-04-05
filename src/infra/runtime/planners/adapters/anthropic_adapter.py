from __future__ import annotations

import json

from src.domain.ports.planner import PlannerRuntimeError, PlannerTool
from src.infra.runtime.planners.runtime_types import (
    NormalizedAssistantTurn,
    NormalizedToolCall,
    NormalizedToolResult,
)


class AnthropicPlannerAdapter:
    def __init__(self, api_key: str, model: str, thinking_budget: int = 8000) -> None:
        try:
            import anthropic
        except ImportError as exc:
            raise PlannerRuntimeError(
                "anthropic SDK not installed. Run: pip install anthropic"
            ) from exc

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._thinking_budget = thinking_budget

    def initial_messages(self, prompt: str) -> list[dict]:
        return [{"role": "user", "content": prompt}]

    def to_provider_tools(self, tools: list[PlannerTool]) -> list[dict]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in tools
        ]

    def send_turn(self, messages: list[dict], provider_tools: list[dict]) -> NormalizedAssistantTurn:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=16000,
            thinking={"type": "enabled", "budget_tokens": self._thinking_budget},
            tools=provider_tools,
            messages=messages,
        )

        reasoning = ""
        final_text = ""
        tool_calls: list[NormalizedToolCall] = []
        serialized_blocks: list[dict] = []

        for block in response.content:
            if block.type == "thinking":
                reasoning = getattr(block, "thinking", "")
            elif block.type == "text":
                final_text = getattr(block, "text", "")
            elif block.type == "tool_use":
                tool_calls.append(
                    NormalizedToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=block.input,
                    )
                )

            if hasattr(block, "model_dump"):
                serialized_blocks.append(block.model_dump())
            elif hasattr(block, "__dict__"):
                serialized_blocks.append({k: v for k, v in vars(block).items() if not k.startswith("_")})
            else:
                serialized_blocks.append({"type": "unknown", "raw": str(block)})

        return NormalizedAssistantTurn(
            content_blocks=serialized_blocks,
            tool_calls=tool_calls,
            final_text=final_text,
            reasoning=reasoning,
            provider_message={"role": "assistant", "content": response.content},
        )

    def append_assistant_turn(self, messages: list[dict], turn: NormalizedAssistantTurn) -> None:
        messages.append(turn.provider_message)

    def append_tool_results(self, messages: list[dict], results: list[NormalizedToolResult]) -> None:
        tool_result_blocks = [
            {
                "type": "tool_result",
                "tool_use_id": result.tool_call_id,
                "content": result.result_str,
            }
            for result in results
        ]
        messages.append({"role": "user", "content": tool_result_blocks})

    def extract_artifact(self, messages: list[dict], submit_tool_name: str, artifact_arg: str) -> dict:
        for msg in messages:
            content = msg.get("content", [])
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use" and block.get("name") == submit_tool_name:
                    raw = block.get("input", {}).get(artifact_arg, "")
                    try:
                        return json.loads(raw)
                    except Exception:
                        pass
        return {}
