"""
tests/unit/infra/test_planner_adapter_errors.py — provider error translation.

A model without tool-use support makes OpenRouter answer the planner's tool
call with a raw 404. The adapters must translate that into a clean
PlannerRuntimeError carrying an actionable message, so the planner session
fails with operator-readable text instead of leaking the provider exception
as a 500.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import openai
import pytest

from src.domain.ports.planner import PlannerRuntimeError
from src.infra.runtime.planners.adapters import classify_provider_error
from src.infra.runtime.planners.adapters.openai_adapter import OpenAIPlannerAdapter


class _FakeAPIError(openai.APIError):
    """Minimal stand-in for a provider APIError with a status_code."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        Exception.__init__(self, message)
        if status_code is not None:
            self.status_code = status_code


class TestClassifyProviderError:
    def test_404_tool_use_message_is_actionable(self):
        exc = _FakeAPIError(
            "No endpoints found that support tool use", status_code=404
        )
        err = classify_provider_error("some/model", exc)
        assert isinstance(err, PlannerRuntimeError)
        assert "some/model" in str(err)
        assert "does not support tool use" in str(err)

    def test_tool_use_phrase_without_404_still_classified(self):
        exc = _FakeAPIError("the model rejected tool_use blocks")
        err = classify_provider_error("m", exc)
        assert "does not support tool use" in str(err)

    def test_generic_error_is_wrapped_cleanly(self):
        exc = _FakeAPIError("rate limit exceeded", status_code=429)
        err = classify_provider_error("m", exc)
        assert "Planner LLM request failed" in str(err)
        assert "rate limit exceeded" in str(err)


class TestOpenAIAdapterSendTurn:
    def test_send_turn_translates_provider_404(self):
        adapter = OpenAIPlannerAdapter(api_key="x", model="vendor/no-tools")
        adapter._client = MagicMock()
        adapter._client.chat.completions.create.side_effect = _FakeAPIError(
            "No endpoints found that support tool use", status_code=404
        )
        with pytest.raises(PlannerRuntimeError) as ei:
            adapter.send_turn(messages=[], provider_tools=[])
        assert "vendor/no-tools" in str(ei.value)
        assert "does not support tool use" in str(ei.value)
