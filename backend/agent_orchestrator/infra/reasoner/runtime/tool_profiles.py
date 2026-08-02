"""Purpose-scoped reasoner tool profiles.

Handlers return DTO/context JSON only. They have no repository or aggregate reference,
so a model can submit a candidate artifact but cannot commit lifecycle state.

Readers come in two shapes. Most are zero-argument context reads and stay
one-liners via `simple_reader`. The repository readers take arguments — a path
to read, a prefix to list, a pattern to search — because repository sight cannot
be pre-computed into a blob: a one-shot dump either overflows the context or
misses the one file that mattered. The model has to be able to ASK.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping

from agent_orchestrator.infra.reasoner.runtime.tools import ToolSpec

_NO_ARGS: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}


@dataclass(frozen=True)
class ReaderSpec:
    """One read tool: what it returns, what it accepts, and how to serve it.

    `description` is not decoration. It used to be auto-generated from the tool
    name ("Read immutable repository context."), which told the model nothing
    about what the call would actually produce — and `read_repository_context`
    then returned a stub saying inspection was unavailable, which reads as a
    licence to guess. A reader that cannot describe its own payload will be
    called blindly or not at all.
    """

    description: str
    handler: Callable[[dict[str, Any]], str]
    input_schema: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.input_schema is None:
            object.__setattr__(self, "input_schema", _NO_ARGS)


def simple_reader(description: str, reader: Callable[[], str]) -> ReaderSpec:
    """A zero-argument context read."""

    def handle(_args: dict[str, Any]) -> str:
        return reader()

    return ReaderSpec(description=description, handler=handle)


class ReasoningPurpose(str, Enum):
    INTENT_DISCOVERY = "intent_discovery"
    CYCLE_ARCHITECTURE = "cycle_architecture"
    GOAL_ENRICHMENT = "goal_enrichment"


# Repository sight is offered where a decision depends on what the code actually
# looks like: the cycle architecture (what work exists) and goal enrichment
# (which exact paths and commands go into a frozen contract). Discovery is a
# conversation about intent and deliberately stays out of the code.
_REPOSITORY_READS = (
    "list_repository_paths",
    "read_repository_file",
    "search_repository",
)

_READ_ALLOWLIST = {
    ReasoningPurpose.INTENT_DISCOVERY: (
        "read_project_spec",
        "read_project_plan",
        "read_repository_context",
        "read_conversation",
    ),
    ReasoningPurpose.CYCLE_ARCHITECTURE: (
        "read_project_spec",
        "read_project_plan",
        "read_repository_context",
        "read_approved_intent",
        "read_prior_evidence",
        *_REPOSITORY_READS,
    ),
    ReasoningPurpose.GOAL_ENRICHMENT: (
        "read_project_spec",
        "read_project_plan",
        "read_repository_context",
        "read_approved_intent",
        "read_active_goal",
        "read_prior_evidence",
        *_REPOSITORY_READS,
    ),
}

# Readers that need a live repository. When sight is unavailable (unresolvable
# project, missing clone) the profile is built WITHOUT them and the prompt says
# so — degraded, not dead. Everything else stays mandatory: a missing context
# reader is a wiring bug.
_OPTIONAL_READS = frozenset(_REPOSITORY_READS)

_SUBMISSION_TOOL = {
    ReasoningPurpose.INTENT_DISCOVERY: "submit_intent_proposal",
    ReasoningPurpose.CYCLE_ARCHITECTURE: "submit_cycle_draft",
    ReasoningPurpose.GOAL_ENRICHMENT: "submit_goal_contract",
}

REPOSITORY_READ_SCHEMAS: dict[str, dict[str, Any]] = {
    "list_repository_paths": {
        "type": "object",
        "properties": {
            "prefix": {
                "type": "string",
                "description": "optional repository-relative directory, e.g. 'tests'",
            }
        },
        "additionalProperties": False,
    },
    "read_repository_file": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "repository-relative file path, e.g. 'src/pkg/mod.py'",
            }
        },
        "required": ["path"],
        "additionalProperties": False,
    },
    "search_repository": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "literal text to find (not a regex)"},
            "path_prefix": {"type": "string", "description": "optional directory to search under"},
        },
        "required": ["pattern"],
        "additionalProperties": False,
    },
}


def allowed_tool_names(
    purpose: ReasoningPurpose, *, repository_available: bool = True
) -> tuple[str, ...]:
    reads = tuple(
        name
        for name in _READ_ALLOWLIST[purpose]
        if repository_available or name not in _OPTIONAL_READS
    )
    return (*reads, _SUBMISSION_TOOL[purpose])


def build_tool_profile(
    purpose: ReasoningPurpose,
    readers: Mapping[str, ReaderSpec],
    submission_schema: dict[str, Any],
    submit: Callable[[dict[str, Any]], str],
) -> list[ToolSpec]:
    """Build only the allowlisted reads plus one stage-specific terminal submit.

    A caller may omit the repository readers (no sight available); omitting any
    other allowlisted reader is a wiring bug and raises.
    """
    missing = [
        name
        for name in _READ_ALLOWLIST[purpose]
        if name not in readers and name not in _OPTIONAL_READS
    ]
    if missing:
        raise ValueError(f"missing reasoner readers for {purpose.value}: {missing}")

    tools = [
        ToolSpec(
            name=name,
            description=readers[name].description,
            input_schema=readers[name].input_schema,
            handler=readers[name].handler,
        )
        for name in _READ_ALLOWLIST[purpose]
        if name in readers
    ]
    tools.append(
        ToolSpec(
            name=_SUBMISSION_TOOL[purpose],
            description=(
                f"Submit a {purpose.value} candidate DTO for application validation. "
                "This does not mutate accepted plan state."
            ),
            input_schema=submission_schema,
            handler=submit,
            terminal=True,
        )
    )
    return tools


class ArtifactCollector:
    """Session-local DTO sink used by submission handlers."""

    def __init__(self) -> None:
        self.value: dict[str, Any] | None = None

    def submit(self, value: dict[str, Any]) -> str:
        self.value = value
        return json.dumps({"accepted": True})


__all__ = [
    "ArtifactCollector",
    "REPOSITORY_READ_SCHEMAS",
    "ReaderSpec",
    "ReasoningPurpose",
    "allowed_tool_names",
    "build_tool_profile",
    "simple_reader",
]
