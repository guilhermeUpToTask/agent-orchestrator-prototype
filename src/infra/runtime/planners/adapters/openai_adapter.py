from __future__ import annotations

import json
import time
from typing import Any, Callable, Optional

import openai
from openai import OpenAI

from src.domain.ports.planner import PlannerRuntimeError, PlannerTool
from src.infra.runtime.planners.adapters import (
    classify_provider_error,
    provider_error_from_empty_choices,
)
from src.infra.runtime.planners.runtime_types import (
    NormalizedAssistantTurn,
    NormalizedToolCall,
    NormalizedToolResult,
)


class OpenAIPlannerAdapter:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: Optional[str] = None,
        system_prompt: Optional[str] = None,
        temperature: float = 0.2,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._temperature = temperature
        self._max_retries = max_retries
        self._sleep = sleep
        self._system_prompt = (
            system_prompt
            or "You are an expert software architect and planner. Use the provided tools to construct a roadmap."
        )

    def initial_messages(self, prompt: str) -> list[dict]:
        return [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": prompt},
        ]

    def messages_from_turns(self, prompt: str, prior_turns: list[dict]) -> list[dict]:
        """Rebuild the provider message history from a persisted transcript.

        ``prior_turns`` are normalized ``SessionTurn`` records ({"role", "content"})
        as saved by the session callback: an ``assistant`` turn carries a single
        block ``{"text", "tool_calls"}``; a ``tool_result`` turn carries blocks
        ``{"tool_use_id", "name", "content"}``. We translate them back into OpenAI
        ``assistant`` / ``tool`` messages so a resumed session continues in context.
        """
        messages = self.initial_messages(prompt)
        for turn in prior_turns:
            role = turn.get("role")
            blocks = turn.get("content") or []
            if role == "assistant":
                block = blocks[0] if blocks else {}
                tool_calls = block.get("tool_calls") or []
                assistant: dict = {"role": "assistant", "content": block.get("text") or None}
                if tool_calls:
                    assistant["tool_calls"] = tool_calls
                messages.append(assistant)
            elif role == "tool_result":
                for b in blocks:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": b.get("tool_use_id"),
                            "content": b.get("content", ""),
                        }
                    )
        _trim_dangling_tool_calls(messages)
        return messages

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

    def _request_message(self, messages: list[dict], provider_tools: list[dict]) -> Any:
        """Call the provider, retrying transient failures with exponential backoff.

        A response carrying no ``choices`` is an in-band provider error (some
        OpenAI-compatible proxies return one with HTTP 200); it is treated as a
        transient failure and retried alongside raised ``openai.APIError``s.
        Permanent failures (e.g. tool-use unsupported) are re-raised immediately.
        """
        last_error: PlannerRuntimeError | None = None
        for attempt in range(self._max_retries):
            try:
                response = self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    tools=provider_tools,
                    temperature=self._temperature,
                )
            except openai.APIError as exc:
                error = classify_provider_error(self._model, exc)
            else:
                if response.choices:
                    return response.choices[0].message
                error = provider_error_from_empty_choices(self._model, response)

            last_error = error
            if not error.transient or attempt == self._max_retries - 1:
                raise error
            self._sleep(2.0 ** attempt)
        assert last_error is not None  # loop always raises on the final attempt
        raise last_error

    def send_turn(self, messages: list[dict], provider_tools: list[dict]) -> NormalizedAssistantTurn:
        msg = self._request_message(messages, provider_tools)

        tool_calls: list[NormalizedToolCall] = []
        for tc in (msg.tool_calls or []):
            if not hasattr(tc, "function"):
                continue  # custom tool calls carry no function payload
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


def _trim_dangling_tool_calls(messages: list[dict]) -> None:
    """Drop a trailing assistant message whose tool_calls have no tool replies.

    A crash mid-tool-execution can persist an assistant turn that called tools
    without the following tool results. OpenAI rejects a request whose final
    assistant message has unanswered tool_calls, so on resume we drop it and let
    the model re-derive its next turn from the answered history.
    """
    while messages and messages[-1].get("role") == "assistant" and messages[-1].get("tool_calls"):
        messages.pop()
