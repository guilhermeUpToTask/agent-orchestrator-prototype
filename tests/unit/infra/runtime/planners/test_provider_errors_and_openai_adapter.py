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

from src.app.telemetry.runtime_wrappers import _model_of
from src.domain.ports.planner import PlannerTool
from src.infra.runtime.planners.adapters import classify_provider_error
from src.infra.runtime.planners.openai_planner_runtime import OpenAIPlannerRuntime
from src.infra.runtime.planners.stub_planner_runtime import StubPlannerRuntime


class TestClassifyProviderError:
    def test_timeout_named_clearly(self):
        exc = type("APITimeoutError", (Exception,), {})("request timed out")
        err = classify_provider_error("claude-sonnet-4-6", exc)
        assert "timed out" in str(err)
        assert "claude-sonnet-4-6" in str(err)

    def test_tool_use_rejection_named(self):
        exc = Exception("model does not support tool use")
        assert "tool use" in str(classify_provider_error("m", exc))

    def test_404_treated_as_tool_use(self):
        exc = type("E", (Exception,), {"status_code": 404})("not found")
        assert "tool-capable" in str(classify_provider_error("m", exc))

    def test_generic_error_wrapped(self):
        err = classify_provider_error("m", Exception("boom"))
        assert "boom" in str(err)


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
