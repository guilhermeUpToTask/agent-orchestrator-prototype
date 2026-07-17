"""OpenAIChatClient request behavior: transient retry with backoff, permanent
fail-fast, the empty-choices in-band error guard, tolerant tool-arg parsing.
The OpenAI SDK is stubbed at the AsyncOpenAI client attribute level — no
network, no provider."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import openai
import pytest

from src.infra.reasoner.runtime.errors import (
    ReasonerError,
    classify_provider_error,
    provider_error_from_empty_choices,
)
from src.infra.reasoner.runtime.llm_client import OpenAIChatClient, to_provider_tools
from src.infra.reasoner.runtime.tools import ToolSpec


def make_client(responses, temperature=0.2, max_retries=3):
    """An OpenAIChatClient whose chat.completions.create pops `responses`
    (an Exception instance raises; anything else returns)."""
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    client = OpenAIChatClient(api_key="k", model="m", max_retries=max_retries, sleep=fake_sleep)
    calls: list[dict] = []

    async def fake_create(**kwargs):
        calls.append(kwargs)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )
    return client, sleeps, calls


def response_with(message):
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def assistant_message(content=None, tool_calls=None):
    return SimpleNamespace(
        content=content,
        tool_calls=tool_calls,
        model_dump=lambda exclude_none=False: {
            "role": "assistant",
            "content": content,
        },
    )


def api_error(message, status_code=None):
    err = openai.APIError(message, request=None, body=None)  # type: ignore[arg-type]
    if status_code is not None:
        err.status_code = status_code  # type: ignore[attr-defined]
    return err


TOOLS = [ToolSpec(name="t", description="d", input_schema={"type": "object"}, handler=lambda a: "")]


def test_transient_error_retries_with_exponential_backoff():
    client, sleeps, calls = make_client(
        [
            api_error("rate limited"),
            api_error("rate limited"),
            response_with(assistant_message("ok")),
        ]
    )
    turn = asyncio.run(client.complete([{"role": "user", "content": "x"}], TOOLS))
    assert turn.text == "ok"
    assert len(calls) == 3
    assert sleeps == [1.0, 2.0]  # 2.0**attempt


def test_permanent_error_fails_fast_without_retry():
    client, sleeps, calls = make_client([api_error("no tool use here", status_code=404)])
    with pytest.raises(ReasonerError) as err:
        asyncio.run(client.complete([], TOOLS))
    assert err.value.transient is False
    assert "does not support tool use" in str(err.value)
    assert len(calls) == 1 and sleeps == []


def test_empty_choices_is_transient_and_retried():
    in_band_error = SimpleNamespace(choices=None, error={"message": "out of credits", "code": 402})
    client, sleeps, _ = make_client([in_band_error, response_with(assistant_message("recovered"))])
    turn = asyncio.run(client.complete([], TOOLS))
    assert turn.text == "recovered"
    assert sleeps == [1.0]


def test_retry_budget_exhaustion_raises_last_error():
    client, _, calls = make_client(
        [api_error("blip"), api_error("blip"), api_error("blip")], max_retries=3
    )
    with pytest.raises(ReasonerError) as err:
        asyncio.run(client.complete([], TOOLS))
    assert err.value.transient is True
    assert len(calls) == 3


def test_malformed_tool_arguments_parse_to_empty_dict():
    tool_call = SimpleNamespace(
        id="c1",
        function=SimpleNamespace(name="t", arguments="{not json"),
    )
    client, _, _ = make_client([response_with(assistant_message(None, [tool_call]))])
    turn = asyncio.run(client.complete([], TOOLS))
    assert turn.tool_calls[0].arguments == {}


def test_provider_tool_wire_shape():
    wire = to_provider_tools(TOOLS)
    assert wire == [
        {
            "type": "function",
            "function": {
                "name": "t",
                "description": "d",
                "parameters": {"type": "object"},
            },
        }
    ]


def test_classify_timeout_is_transient():
    err = classify_provider_error("m", TimeoutError("request timed out"))
    assert err.transient is True and "timed out" in str(err)


def test_empty_choices_error_extracts_object_shaped_payload():
    response = SimpleNamespace(
        choices=None,
        error=SimpleNamespace(message="upstream 502", code=502),
    )
    err = provider_error_from_empty_choices("m", response)
    assert err.transient is True
    assert "upstream 502" in str(err) and "code=502" in str(err)


def test_tool_arguments_json_but_not_object_coerce_to_empty_dict():
    tool_call = SimpleNamespace(
        id="c1",
        function=SimpleNamespace(name="t", arguments=json.dumps([1, 2])),
    )
    client, _, _ = make_client([response_with(assistant_message(None, [tool_call]))])
    turn = asyncio.run(client.complete([], TOOLS))
    assert turn.tool_calls[0].arguments == {}
