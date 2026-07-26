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

from src.domain.value_objects.lifecycle import FailureKind
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
    assert err.value.kind is FailureKind.TOOL_ERROR
    assert "does not support tool use" in str(err.value)
    assert len(calls) == 1 and sleeps == []


def test_empty_choices_is_transient_and_retried():
    in_band_error = SimpleNamespace(choices=None, error={"message": "out of credits", "code": 402})
    client, sleeps, _ = make_client([in_band_error, response_with(assistant_message("recovered"))])
    turn = asyncio.run(client.complete([], TOOLS))
    assert turn.text == "recovered"
    assert sleeps == [1.0]


def response_with_choice(message, finish_reason="stop"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)]
    )


def test_upstream_error_inside_a_200_choice_is_transient_and_retried():
    """OpenRouter returns some upstream failures as a well-formed choice with
    `finish_reason="error"`, no content and no tool calls — HTTP 200, `choices`
    present, so neither the SDK nor the empty-choices guard sees it.

    Observed live against openai/gpt-oss-20b:free: 164 reasoning tokens billed,
    an empty message, and the agent loop reported "Reasoner replied with plain
    text where a tool submit was required" — blaming the model for a provider
    fault, and abandoning a session that had already paid for three read turns.
    """
    failed = response_with_choice(assistant_message(None), finish_reason="error")
    client, sleeps, calls = make_client(
        [failed, response_with_choice(assistant_message("recovered"))]
    )

    turn = asyncio.run(client.complete([], TOOLS))

    assert turn.text == "recovered"
    assert len(calls) == 2 and sleeps == [1.0]


def test_a_choice_with_neither_content_nor_tool_calls_is_treated_as_a_provider_fault():
    """A turn that carries nothing is never a usable answer, whatever the
    provider labels it. Retrying costs one call; surfacing it as a model
    failure costs the whole session."""
    empty = response_with_choice(assistant_message(""), finish_reason="stop")
    client, sleeps, calls = make_client([empty, response_with_choice(assistant_message("ok"))])

    turn = asyncio.run(client.complete([], TOOLS))

    assert turn.text == "ok"
    assert len(calls) == 2


def test_an_empty_content_turn_that_carries_tool_calls_is_kept():
    """Tool-calling models routinely answer with tool_calls and no prose. That
    is the normal success path and must not be mistaken for a fault."""
    tool_call = SimpleNamespace(id="c1", function=SimpleNamespace(name="t", arguments="{}"))
    client, _, calls = make_client(
        [response_with_choice(assistant_message(None, [tool_call]), finish_reason="tool_calls")]
    )

    turn = asyncio.run(client.complete([], TOOLS))

    assert [call.name for call in turn.tool_calls] == ["t"]
    assert len(calls) == 1


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
    assert err.kind is FailureKind.TIMEOUT


def test_classify_generic_error_yields_connection_error_kind():
    err = classify_provider_error("m", api_error("upstream blip"))
    assert err.transient is True
    assert err.kind is FailureKind.CONNECTION_ERROR


def test_classify_429_status_code_yields_rate_limit_kind():
    err = classify_provider_error("m", api_error("slow down", status_code=429))
    assert err.transient is True
    assert err.kind is FailureKind.RATE_LIMIT


def test_classify_provider_error_surfaces_retry_after_seconds():
    err = classify_provider_error(
        "m", api_error("rate limited, retry after 30s", status_code=429)
    )
    assert err.kind is FailureKind.RATE_LIMIT
    assert err.retry_after_seconds == 30.0


def test_empty_choices_error_extracts_object_shaped_payload():
    response = SimpleNamespace(
        choices=None,
        error=SimpleNamespace(message="upstream 502", code=502),
    )
    err = provider_error_from_empty_choices("m", response)
    assert err.transient is True
    assert "upstream 502" in str(err) and "code=502" in str(err)
    # "upstream 502" names no rate-limit/quota/credit/resource-exhausted
    # condition, so this falls back to CONNECTION_ERROR.
    assert err.kind is FailureKind.CONNECTION_ERROR


def test_empty_choices_naming_rate_limit_yields_rate_limit_kind():
    response = SimpleNamespace(
        choices=None,
        error={"message": "Rate limit exceeded, please retry later", "code": 429},
    )
    err = provider_error_from_empty_choices("m", response)
    assert err.kind is FailureKind.RATE_LIMIT


def test_empty_choices_naming_credits_yields_rate_limit_kind():
    response = SimpleNamespace(
        choices=None,
        error={"message": "You have run out of credits", "code": 402},
    )
    err = provider_error_from_empty_choices("m", response)
    assert err.kind is FailureKind.RATE_LIMIT


def test_empty_choices_retry_after_surfaced():
    response = SimpleNamespace(
        choices=None,
        error={"message": "rate limited, retry after 12s", "code": 429},
    )
    err = provider_error_from_empty_choices("m", response)
    assert err.kind is FailureKind.RATE_LIMIT
    assert err.retry_after_seconds == 12.0


def test_tool_arguments_json_but_not_object_coerce_to_empty_dict():
    tool_call = SimpleNamespace(
        id="c1",
        function=SimpleNamespace(name="t", arguments=json.dumps([1, 2])),
    )
    client, _, _ = make_client([response_with(assistant_message(None, [tool_call]))])
    turn = asyncio.run(client.complete([], TOOLS))
    assert turn.tool_calls[0].arguments == {}
