"""
tests/unit/infra/runtime/planners/test_provider_errors_and_openai_adapter.py

Guards the provider-neutral planner runtime:
  - provider errors (incl. timeouts and tool-use rejections) become
    actionable ``PlannerRuntimeError`` messages,
  - the dry-run stub satisfies the JIT submit_tdd_tasks tool,
  - telemetry resolves the real model id instead of reporting ``unknown``,
  - the OpenAI runtime requires an explicit model (no default).
"""
from __future__ import annotations

import json

import httpx
import openai
import pytest

from src.app.telemetry.runtime_wrappers import _model_of
from src.domain.ports.planner import PlannerRuntimeError, PlannerTool
from src.infra.runtime.planners.adapters import (
    classify_provider_error,
    provider_error_from_empty_choices,
)
from src.infra.runtime.planners.adapters.openai_adapter import OpenAIPlannerAdapter
from src.infra.runtime.planners.openai_planner_runtime import OpenAIPlannerRuntime
from src.infra.runtime.planners.stub_planner_runtime import StubPlannerRuntime


class TestClassifyProviderError:
    def test_timeout_named_clearly(self):
        exc = type("APITimeoutError", (Exception,), {})("request timed out")
        err = classify_provider_error("claude-sonnet-4-6", exc)
        assert "timed out" in str(err)
        assert "claude-sonnet-4-6" in str(err)
        assert err.transient is True

    def test_tool_use_rejection_named(self):
        exc = Exception("model does not support tool use")
        err = classify_provider_error("m", exc)
        assert "tool use" in str(err)
        assert err.transient is False  # config error, not worth retrying

    def test_404_treated_as_tool_use(self):
        exc = type("E", (Exception,), {"status_code": 404})("not found")
        err = classify_provider_error("m", exc)
        assert "tool-capable" in str(err)
        assert err.transient is False

    def test_generic_error_wrapped(self):
        err = classify_provider_error("m", Exception("boom"))
        assert "boom" in str(err)
        assert err.transient is True


class TestEmptyChoices:
    """A 200 response with no choices (in-band provider error) must become an
    actionable PlannerRuntimeError instead of an opaque NoneType crash."""

    def test_dict_shaped_error(self):
        response = type(
            "R", (), {"error": {"message": "insufficient credits", "code": 402}}
        )()
        err = provider_error_from_empty_choices("gpt-4o", response)
        assert "gpt-4o" in str(err)
        assert "insufficient credits" in str(err)
        assert "402" in str(err)

    def test_object_shaped_error(self):
        error = type("E", (), {"message": "upstream boom", "code": None})()
        response = type("R", (), {"error": error})()
        err = provider_error_from_empty_choices("m", response)
        assert "upstream boom" in str(err)

    def test_no_error_falls_back_to_dump(self):
        response = type("R", (), {"model_dump": lambda self: {"id": "x"}})()
        err = provider_error_from_empty_choices("m", response)
        assert "id" in str(err)
        assert err.transient is True

    def test_send_turn_raises_on_none_choices(self):
        client = _FakeClient([_empty_response("no provider")])
        adapter = _adapter_with(client, max_retries=1)
        with pytest.raises(PlannerRuntimeError) as exc_info:
            adapter.send_turn([], [])
        assert "gpt-4o" in str(exc_info.value)
        assert "no provider" in str(exc_info.value)


class TestSendTurnRetry:
    """Transient LLM failures are retried with backoff; permanent ones are not."""

    def test_retries_transient_then_succeeds(self):
        client = _FakeClient([_empty_response(), _empty_response(), _ok_response("done")])
        adapter = _adapter_with(client, max_retries=3)
        turn = adapter.send_turn([], [])
        assert turn.final_text == "done"
        assert client.completions.calls == 3

    def test_raises_after_exhausting_retries(self):
        client = _FakeClient([_empty_response(), _empty_response(), _empty_response()])
        adapter = _adapter_with(client, max_retries=3)
        with pytest.raises(PlannerRuntimeError):
            adapter.send_turn([], [])
        assert client.completions.calls == 3

    def test_does_not_retry_permanent_error(self):
        api_err = openai.APIError(
            "model does not support tool use",
            httpx.Request("POST", "http://x"),
            body=None,
        )
        client = _FakeClient([api_err])
        adapter = _adapter_with(client, max_retries=3)
        with pytest.raises(PlannerRuntimeError) as exc_info:
            adapter.send_turn([], [])
        assert "tool use" in str(exc_info.value)
        assert client.completions.calls == 1  # not retried


class TestMessagesFromTurns:
    """A persisted transcript rebuilds into OpenAI provider messages on resume."""

    def test_rebuilds_assistant_and_tool_messages(self):
        adapter = OpenAIPlannerAdapter(api_key="k", model="m")
        turns = [
            {
                "role": "assistant",
                "content": [
                    {
                        "text": "What entities?",
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {"name": "ask_question", "arguments": "{}"},
                            }
                        ],
                    }
                ],
            },
            {
                "role": "tool_result",
                "content": [
                    {"tool_use_id": "c1", "name": "ask_question", "content": '{"answer": "products"}'}
                ],
            },
        ]
        msgs = adapter.messages_from_turns("PROMPT", turns)
        assert msgs[0]["role"] == "system"
        assert msgs[1] == {"role": "user", "content": "PROMPT"}
        assert msgs[2]["role"] == "assistant"
        assert msgs[2]["tool_calls"][0]["id"] == "c1"
        assert msgs[3] == {"role": "tool", "tool_call_id": "c1", "content": '{"answer": "products"}'}

    def test_trims_dangling_trailing_assistant_tool_call(self):
        adapter = OpenAIPlannerAdapter(api_key="k", model="m")
        turns = [
            {"role": "tool_result", "content": [{"tool_use_id": "c0", "name": "x", "content": "ok"}]},
            {
                "role": "assistant",
                "content": [
                    {
                        "text": "",
                        "tool_calls": [
                            {"id": "c1", "type": "function", "function": {"name": "ask_question", "arguments": "{}"}}
                        ],
                    }
                ],
            },
        ]
        msgs = adapter.messages_from_turns("p", turns)
        # The dangling assistant (unanswered tool_call) is dropped.
        assert msgs[-1]["role"] == "tool"
        assert all(not (m["role"] == "assistant" and m.get("tool_calls")) for m in msgs)


# --- fakes for the OpenAI client -------------------------------------------


class _FakeMessage:
    def __init__(self, content: str = "hi") -> None:
        self.content = content
        self.tool_calls = None

    def model_dump(self, exclude_none: bool = False) -> dict:
        return {"role": "assistant", "content": self.content}


def _ok_response(content: str = "hi"):
    choice = type("Choice", (), {"message": _FakeMessage(content)})()
    return type("R", (), {"choices": [choice]})()


def _empty_response(error_message: str = "rate limited"):
    return type("R", (), {"choices": None, "error": {"message": error_message}})()


class _FakeCompletions:
    def __init__(self, scripted: list) -> None:
        self._scripted = list(scripted)
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        item = self._scripted.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeClient:
    def __init__(self, scripted: list) -> None:
        self.completions = _FakeCompletions(scripted)
        self.chat = type("C", (), {"completions": self.completions})()


def _adapter_with(client: _FakeClient, max_retries: int) -> OpenAIPlannerAdapter:
    adapter = OpenAIPlannerAdapter(
        api_key="k", model="gpt-4o", max_retries=max_retries, sleep=lambda _s: None
    )
    adapter._client = client
    return adapter


class TestStubJitTasks:
    """The dry-run stub must satisfy the JIT submit_tdd_tasks tool so goals
    actually get populated (it previously produced 0 tasks → JIT no-op)."""

    def test_stub_supplies_two_tdd_tasks(self):
        captured: dict = {}

        def handler(inp):
            captured["tasks_json"] = inp.get("tasks_json", "[]")
            return json.dumps({"accepted": True})

        tool = PlannerTool(
            name="submit_tdd_tasks",
            description="submit",
            input_schema={"type": "object", "properties": {}},
            handler=handler,
            terminal=True,
        )
        StubPlannerRuntime().run_session(prompt="p", tools=[tool], require_submit=False)

        tasks = json.loads(captured["tasks_json"])
        assert len(tasks) == 2
        assert tasks[0]["task_id"] == "write-tests"
        assert tasks[1]["depends_on"] == ["write-tests"]


class TestModelOf:
    def test_reads_public_model_property(self):
        wrapped = type("R", (), {"model": "claude-opus-4-8"})()
        assert _model_of(wrapped) == "claude-opus-4-8"

    def test_falls_back_to_private_model(self):
        wrapped = type("R", (), {"_model": "gpt-4o"})()
        assert _model_of(wrapped) == "gpt-4o"

    def test_unknown_when_absent(self):
        assert _model_of(object()) == "unknown"


class TestOpenAIRuntimeModel:
    def test_model_required_and_surfaced(self):
        # Model is explicit (no default) and exposed for telemetry.
        runtime = OpenAIPlannerRuntime(api_key="k", model="gpt-4o")
        assert runtime.model == "gpt-4o"
        assert _model_of(runtime) == "gpt-4o"
