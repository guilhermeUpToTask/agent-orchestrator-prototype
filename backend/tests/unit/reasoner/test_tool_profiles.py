from __future__ import annotations

import json

import pytest

from src.infra.reasoner.runtime.tool_profiles import (
    ArtifactCollector,
    ReasoningPurpose,
    allowed_tool_names,
    build_tool_profile,
)
from src.infra.reasoner.runtime.tools import ToolCall, execute_tool_call


EXPECTED = {
    ReasoningPurpose.INTENT_DISCOVERY: {
        "read_project_spec",
        "read_project_plan",
        "read_repository_context",
        "read_conversation",
        "submit_intent_proposal",
    },
    ReasoningPurpose.CYCLE_ARCHITECTURE: {
        "read_project_spec",
        "read_project_plan",
        "read_repository_context",
        "read_approved_intent",
        "read_prior_evidence",
        "submit_cycle_draft",
    },
    ReasoningPurpose.GOAL_ENRICHMENT: {
        "read_project_spec",
        "read_project_plan",
        "read_repository_context",
        "read_approved_intent",
        "read_active_goal",
        "read_prior_evidence",
        "submit_goal_contract",
    },
}


@pytest.mark.parametrize("purpose", list(ReasoningPurpose))
def test_reasoner_profiles_expose_only_the_stage_allowlist(purpose: ReasoningPurpose) -> None:
    collector = ArtifactCollector()
    readers = {
        name: (lambda name=name: json.dumps({"reader": name}))
        for name in allowed_tool_names(purpose)
        if name.startswith("read_")
    }
    tools = build_tool_profile(
        purpose,
        readers,
        {"type": "object"},
        collector.submit,
    )
    assert {tool.name for tool in tools} == EXPECTED[purpose]
    assert sum(tool.terminal for tool in tools) == 1


@pytest.mark.parametrize("purpose", list(ReasoningPurpose))
def test_cross_stage_submission_is_unavailable(purpose: ReasoningPurpose) -> None:
    collector = ArtifactCollector()
    readers = {
        name: (lambda: "{}") for name in allowed_tool_names(purpose) if name.startswith("read_")
    }
    tools = build_tool_profile(purpose, readers, {"type": "object"}, collector.submit)
    forbidden = next(
        name
        for expected_purpose, names in EXPECTED.items()
        if expected_purpose != purpose
        for name in names
        if name.startswith("submit_") and name not in EXPECTED[purpose]
    )
    result = execute_tool_call(
        tools,
        ToolCall(id="call-1", name=forbidden, arguments={"malicious": True}),
    )
    assert "Unknown tool" in result.result_str
    assert collector.value is None


def test_submission_collects_dto_without_an_aggregate_or_repository() -> None:
    collector = ArtifactCollector()
    purpose = ReasoningPurpose.INTENT_DISCOVERY
    readers = {
        name: (lambda: "{}") for name in allowed_tool_names(purpose) if name.startswith("read_")
    }
    tools = build_tool_profile(purpose, readers, {"type": "object"}, collector.submit)
    result = execute_tool_call(
        tools,
        ToolCall(
            id="call-1",
            name="submit_intent_proposal",
            arguments={"objective": "ship safely"},
        ),
    )
    assert json.loads(result.result_str) == {"accepted": True}
    assert collector.value == {"objective": "ship safely"}
