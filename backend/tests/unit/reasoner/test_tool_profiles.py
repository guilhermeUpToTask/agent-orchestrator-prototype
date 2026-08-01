from __future__ import annotations

import json

import pytest

from agent_orchestrator.infra.reasoner.runtime.tool_profiles import (
    REPOSITORY_READ_SCHEMAS,
    ArtifactCollector,
    ReaderSpec,
    ReasoningPurpose,
    allowed_tool_names,
    build_tool_profile,
    simple_reader,
)
from agent_orchestrator.infra.reasoner.runtime.tools import ToolCall, execute_tool_call


# Repository sight is offered where the decision depends on what the code
# actually looks like. Discovery is a conversation about intent and stays out.
REPOSITORY_READS = {
    "list_repository_paths",
    "read_repository_file",
    "search_repository",
}

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
        *REPOSITORY_READS,
        "submit_cycle_draft",
    },
    ReasoningPurpose.GOAL_ENRICHMENT: {
        "read_project_spec",
        "read_project_plan",
        "read_repository_context",
        "read_approved_intent",
        "read_active_goal",
        "read_prior_evidence",
        *REPOSITORY_READS,
        "submit_goal_contract",
    },
}


def _readers(purpose: ReasoningPurpose, *, repository: bool = True) -> dict[str, ReaderSpec]:
    """Mirrors how OpenAIReasoner builds them: context reads are zero-argument,
    repository reads carry the argument schemas the model needs to ask."""
    readers: dict[str, ReaderSpec] = {}
    for name in allowed_tool_names(purpose, repository_available=repository):
        if name.startswith("submit_"):
            continue
        if name in REPOSITORY_READ_SCHEMAS:
            readers[name] = ReaderSpec(
                description=f"repository read: {name}",
                input_schema=REPOSITORY_READ_SCHEMAS[name],
                handler=lambda args, name=name: json.dumps({"reader": name, "args": args}),
            )
        else:
            readers[name] = simple_reader(
                f"read {name}", lambda name=name: json.dumps({"reader": name})
            )
    return readers


@pytest.mark.parametrize("purpose", list(ReasoningPurpose))
def test_reasoner_profiles_expose_only_the_stage_allowlist(purpose: ReasoningPurpose) -> None:
    collector = ArtifactCollector()
    tools = build_tool_profile(purpose, _readers(purpose), {"type": "object"}, collector.submit)

    assert {tool.name for tool in tools} == EXPECTED[purpose]
    assert sum(tool.terminal for tool in tools) == 1


@pytest.mark.parametrize("purpose", list(ReasoningPurpose))
def test_cross_stage_submission_is_unavailable(purpose: ReasoningPurpose) -> None:
    collector = ArtifactCollector()
    tools = build_tool_profile(purpose, _readers(purpose), {"type": "object"}, collector.submit)
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
    tools = build_tool_profile(purpose, _readers(purpose), {"type": "object"}, collector.submit)
    result = execute_tool_call(
        tools,
        ToolCall(id="call-1", name="submit_intent_proposal", arguments={"objective": "ship safely"}),
    )

    assert json.loads(result.result_str) == {"accepted": True}
    assert collector.value == {"objective": "ship safely"}


@pytest.mark.parametrize(
    "purpose", [ReasoningPurpose.CYCLE_ARCHITECTURE, ReasoningPurpose.GOAL_ENRICHMENT]
)
def test_missing_repository_sight_degrades_instead_of_failing(purpose: ReasoningPurpose) -> None:
    """An unresolvable project (no clone, bad url) must not kill planning. The
    profile is built without the repository tools; every other reader stays
    mandatory, because a missing context reader is a wiring bug."""
    collector = ArtifactCollector()
    tools = build_tool_profile(
        purpose, _readers(purpose, repository=False), {"type": "object"}, collector.submit
    )

    assert {tool.name for tool in tools} == EXPECTED[purpose] - REPOSITORY_READS


@pytest.mark.parametrize("purpose", list(ReasoningPurpose))
def test_a_missing_context_reader_is_a_wiring_bug(purpose: ReasoningPurpose) -> None:
    readers = _readers(purpose)
    readers.pop("read_project_plan")

    with pytest.raises(ValueError, match="missing reasoner readers"):
        build_tool_profile(purpose, readers, {"type": "object"}, ArtifactCollector().submit)


def test_repository_readers_declare_their_arguments() -> None:
    """A repository read is useless as a zero-argument tool: the model has to be
    able to name the path it wants. This is why ReaderSpec carries a schema."""
    collector = ArtifactCollector()
    tools = build_tool_profile(
        ReasoningPurpose.GOAL_ENRICHMENT,
        _readers(ReasoningPurpose.GOAL_ENRICHMENT),
        {"type": "object"},
        collector.submit,
    )
    by_name = {tool.name: tool for tool in tools}

    assert by_name["read_repository_file"].input_schema["required"] == ["path"]
    assert by_name["search_repository"].input_schema["required"] == ["pattern"]
    assert by_name["list_repository_paths"].input_schema["properties"].keys() == {"prefix"}
    # a zero-argument context reader keeps the closed empty schema
    assert by_name["read_project_plan"].input_schema["properties"] == {}


def test_every_tool_describes_what_it_returns() -> None:
    """Descriptions used to be auto-generated from the tool name ("Read
    immutable repository context."), which told the model nothing — and the
    repository reader then answered that inspection was unavailable, which reads
    as a licence to guess."""
    collector = ArtifactCollector()
    tools = build_tool_profile(
        ReasoningPurpose.GOAL_ENRICHMENT,
        _readers(ReasoningPurpose.GOAL_ENRICHMENT),
        {"type": "object"},
        collector.submit,
    )

    for tool in tools:
        assert tool.description and not tool.description.startswith("Read immutable")
