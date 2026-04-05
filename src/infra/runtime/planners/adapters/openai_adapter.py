from __future__ import annotations

import json
from typing import Optional

from openai import OpenAI

from src.domain.ports.planner import PlannerTool
from src.infra.runtime.planners.runtime_types import (
    NormalizedAssistantTurn,
    NormalizedToolCall,
    NormalizedToolResult,
)


class OpenAIPlannerAdapter:
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        base_url: Optional[str] = None,
        system_prompt: Optional[str] = None,
        temperature: float = 0.2,
    ) -> None:
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._temperature = temperature
        self._system_prompt = (
            system_prompt
            or "You are an expert software architect and planner. Use the provided tools to construct a roadmap."
        )

    def initial_messages(self, prompt: str) -> list[dict]:
        return [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": prompt},
        ]

    def to_provider_tools(self, tools: list[PlannerTool]) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in tools
        ]

    def send_turn(self, messages: list[dict], provider_tools: list[dict]) -> NormalizedAssistantTurn:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            tools=provider_tools,
            temperature=self._temperature,
        )
        msg = response.choices[0].message

        tool_calls: list[NormalizedToolCall] = []
        for tc in (msg.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments)
            except Exception:
                args = {}
            tool_calls.append(
                NormalizedToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                )
            )

        content_blocks = [
            {
                "text": msg.content or "",
                "tool_calls": [tc.model_dump() for tc in (msg.tool_calls or [])],
            }
        ]

        reasoning = ""
        if hasattr(msg, "reasoning_content") and msg.reasoning_content:
            reasoning = msg.reasoning_content

        return NormalizedAssistantTurn(
            content_blocks=content_blocks,
            tool_calls=tool_calls,
            final_text=msg.content or "",
            reasoning=reasoning,
            provider_message=msg.model_dump(exclude_none=True),
        )

    def append_assistant_turn(self, messages: list[dict], turn: NormalizedAssistantTurn) -> None:
        messages.append(turn.provider_message)

    def append_tool_results(self, messages: list[dict], results: list[NormalizedToolResult]) -> None:
        for result in results:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": result.tool_call_id,
                    "content": result.result_str,
                }
            )

    def extract_artifact(self, messages: list[dict], submit_tool_name: str, artifact_arg: str) -> dict:
        for msg in messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg.get("tool_calls", []):
                    if tc.get("function", {}).get("name") == submit_tool_name:
                        try:
                            args = json.loads(tc["function"]["arguments"])
                        except Exception:
                            continue
                        raw = args.get(artifact_arg, "")
                        if isinstance(raw, dict):
                            return raw
                        if isinstance(raw, str):
                            try:
                                return json.loads(raw)
                            except Exception:
                                return {}
        return {}
