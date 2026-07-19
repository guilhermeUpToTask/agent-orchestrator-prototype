"""Purpose-scoped reasoner tool profiles.

Handlers return DTO/context JSON only. They have no repository or aggregate reference,
so a model can submit a candidate artifact but cannot commit lifecycle state.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any, Callable, Mapping

from src.infra.reasoner.runtime.tools import ToolSpec


class ReasoningPurpose(str, Enum):
    INTENT_DISCOVERY = "intent_discovery"
    CYCLE_ARCHITECTURE = "cycle_architecture"
    GOAL_ENRICHMENT = "goal_enrichment"


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
    ),
    ReasoningPurpose.GOAL_ENRICHMENT: (
        "read_project_spec",
        "read_project_plan",
        "read_repository_context",
        "read_approved_intent",
        "read_active_goal",
        "read_prior_evidence",
    ),
}

_SUBMISSION_TOOL = {
    ReasoningPurpose.INTENT_DISCOVERY: "submit_intent_proposal",
    ReasoningPurpose.CYCLE_ARCHITECTURE: "submit_cycle_draft",
    ReasoningPurpose.GOAL_ENRICHMENT: "submit_goal_contract",
}


def allowed_tool_names(purpose: ReasoningPurpose) -> tuple[str, ...]:
    return (*_READ_ALLOWLIST[purpose], _SUBMISSION_TOOL[purpose])


def _reader_handler(reader: Callable[[], str]) -> Callable[[dict[str, Any]], str]:
    def handle(_args: dict[str, Any]) -> str:
        return reader()

    return handle


def build_tool_profile(
    purpose: ReasoningPurpose,
    readers: Mapping[str, Callable[[], str]],
    submission_schema: dict[str, Any],
    submit: Callable[[dict[str, Any]], str],
) -> list[ToolSpec]:
    """Build only the allowlisted reads plus one stage-specific terminal submit."""
    missing = [name for name in _READ_ALLOWLIST[purpose] if name not in readers]
    if missing:
        raise ValueError(f"missing reasoner readers for {purpose.value}: {missing}")

    tools = [
        ToolSpec(
            name=name,
            description=f"Read immutable {name.removeprefix('read_').replace('_', ' ')} context.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            handler=_reader_handler(readers[name]),
        )
        for name in _READ_ALLOWLIST[purpose]
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
