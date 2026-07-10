"""
The OpenAI-compatible chat client (async) — the old adapter's request layer.

LLMClient is the seam the agent loop runs on: FakeLLMClient (tests) and
OpenAIChatClient (production) implement it. The client owns provider I/O
ONLY — retry with transient/permanent classification, the in-band
empty-choices guard, tolerant tool-argument parsing. The turn loop, tool
dispatch and message history belong to agent_loop.py.

AsyncOpenAI is used because the reasoner runs inside the worker's event loop;
the sync SDK would block the whole worker between turns.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

import openai
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict

from src.infra.reasoner.runtime.errors import (
    ReasonerError,
    classify_provider_error,
    provider_error_from_empty_choices,
)
from src.infra.reasoner.runtime.tools import ToolCall, ToolSpec


class AssistantTurn(BaseModel):
    """One normalized assistant response: display text, parsed tool calls, the
    raw provider message (appended verbatim to keep the transcript valid), and
    the token usage for this call (None when the provider omits it)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    text: str
    tool_calls: list[ToolCall]
    raw_message: dict[str, Any]
    usage: dict[str, int] | None = None


@runtime_checkable
class LLMClient(Protocol):
    async def complete(
        self, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> AssistantTurn: ...


def to_provider_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    """The OpenAI function-calling wire shape."""
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


class OpenAIChatClient:
    """chat.completions against any OpenAI-compatible base_url (OpenAI,
    OpenRouter, Anthropic's compat endpoint, Gemini's, local servers)."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
        temperature: float = 0.2,
        max_retries: int = 3,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self._temperature = temperature
        self._max_retries = max_retries
        self._sleep = sleep

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> AssistantTurn:
        msg, usage = await self._request_message(messages, to_provider_tools(tools))

        tool_calls: list[ToolCall] = []
        for tc in msg.tool_calls or []:
            if not hasattr(tc, "function"):
                continue  # custom tool calls carry no function payload
            try:
                args = json.loads(tc.function.arguments)
            except Exception:
                args = {}
            if not isinstance(args, dict):
                args = {}
            tool_calls.append(
                ToolCall(id=tc.id, name=tc.function.name, arguments=args)
            )

        return AssistantTurn(
            text=msg.content or "",
            tool_calls=tool_calls,
            raw_message=msg.model_dump(exclude_none=True),
            usage=usage,
        )

    @staticmethod
    def _extract_usage(response: Any) -> dict[str, int] | None:
        """Normalize the provider's token usage into prompt/completion/total.
        Returns None when the provider omits usage (never breaks a live call)."""
        usage = getattr(response, "usage", None)
        if usage is None:
            return None
        out: dict[str, int] = {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = getattr(usage, key, None)
            if isinstance(value, int):
                out[key] = value
        return out or None

    async def _request_message(
        self, messages: list[dict[str, Any]], provider_tools: list[dict[str, Any]]
    ) -> tuple[Any, dict[str, int] | None]:
        """Call the provider, retrying transient failures with exponential
        backoff. A response carrying no ``choices`` is an in-band provider
        error (some OpenAI-compatible proxies return one with HTTP 200); it is
        treated as transient and retried alongside raised ``openai.APIError``s.
        Permanent failures (e.g. tool-use unsupported) re-raise immediately.
        """
        last_error: ReasonerError | None = None
        for attempt in range(self._max_retries):
            try:
                response = await self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,  # type: ignore[arg-type]
                    tools=provider_tools,  # type: ignore[arg-type]
                    temperature=self._temperature,
                )
            except openai.APIError as exc:
                error = classify_provider_error(self.model, exc)
            else:
                if response.choices:
                    return response.choices[0].message, self._extract_usage(response)
                error = provider_error_from_empty_choices(self.model, response)

            last_error = error
            if not error.transient or attempt == self._max_retries - 1:
                raise error
            await self._sleep(2.0**attempt)
        assert last_error is not None  # loop always raises on the final attempt
        raise last_error
