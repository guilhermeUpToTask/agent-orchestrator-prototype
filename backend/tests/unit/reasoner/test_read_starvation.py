"""A model that keeps reading must still reach a submission.

The 2026-08-09 starvation defect in one file, driven by a client that *chooses*
to keep reading rather than replaying a script. The distinction is the point: a
scripted fake emits whatever a test author thought to write, so it can only
reproduce a bug someone already understood. `GreedyReaderLLMClient` reacts to
the tools it is OFFERED, which is what makes it able to fail on a reserve that
was never applied.
"""

from __future__ import annotations

import asyncio

import pytest

from agent_orchestrator.domain.entities.capability import Capability
from agent_orchestrator.domain.entities.planning_artifacts import IntentProposal, ProposalKind
from agent_orchestrator.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from agent_orchestrator.infra.reasoner.openai_reasoner import OpenAIReasoner
from tests.fakes_llm import GreedyReaderLLMClient, SilentLLMClient
from datetime import datetime, timezone

T0 = datetime(2026, 7, 3, tzinfo=timezone.utc)
CAPS = [Capability(id="backend", name="Backend", description="")]

INTENT_ARGS = {
    "normalized_brief": "build a static site generator",
    "objective": "a static site generator",
    "constraints": [],
    "open_questions": [],
}
DRAFT_ARGS = {
    "goals": [
        {
            "key": "delivery",
            "name": "Delivery",
            "objective": "ship",
            "position": 0,
            "depends_on": [],
        }
    ]
}


def _plan() -> Plan:
    return Plan(project_id="project-1", id="p1", brief="tiny service", phase=PlanPhase.DISCOVERY)


def _approved_plan() -> Plan:
    plan = _plan()
    plan.intent_proposal = IntentProposal(
        id="intent-1",
        kind=ProposalKind.INITIAL,
        base_plan_version=0,
        objective="ship a static site generator",
        approved_at=T0,
    )
    return plan


def test_a_model_that_only_reads_still_submits_an_intent() -> None:
    """Reverting `reserved_submit_turns` on `converse` makes this fail with the
    exact production message: `exceeded N turns without submitting (N spent on
    reads, 0 on submissions)`."""
    client = GreedyReaderLLMClient(INTENT_ARGS)
    reasoner = OpenAIReasoner(client, CAPS, converse_max_turns=6)

    reply = asyncio.run(reasoner.converse(_plan(), [], "build me a generator", "discovery"))

    assert reply.intent is not None, "a read-happy model never reached a submission"
    # The readers were withdrawn: the final request offered submission only.
    assert client.calls[-1]["tool_names"] == ["submit_intent_proposal"]


def test_a_model_that_only_reads_still_submits_a_cycle_draft() -> None:
    client = GreedyReaderLLMClient(DRAFT_ARGS)
    reasoner = OpenAIReasoner(client, CAPS, converse_max_turns=6)

    outlines = asyncio.run(reasoner.architect_cycle(_approved_plan()))

    assert [o.key for o in outlines] == ["delivery"]
    assert client.calls[-1]["tool_names"] == ["submit_cycle_draft"]


def test_an_empty_completion_in_discovery_returns_an_empty_reply() -> None:
    """Documents REAL behaviour, verified rather than assumed.

    `converse` runs with `allow_plain_reply=True` because a clarifying question
    legitimately has no tool call, so a turn with no text AND no tool calls is
    accepted as the reply — and the operator receives `''`. It does not raise,
    does not nudge, and costs exactly one call.

    That is defensible for the plain-reply contract and still worth pinning: an
    EMPTY reply is not a question, and it reaches the operator looking like the
    reasoner answered. Recorded here as observed behaviour, deliberately NOT
    "fixed" in the same breath — changing it is a product decision about what an
    empty turn means, not a bug fix, and an earlier claim in this project about
    empty completions had to be retracted for exactly that kind of haste.
    """
    client = SilentLLMClient()
    reasoner = OpenAIReasoner(client, CAPS, converse_max_turns=3)

    reply = asyncio.run(reasoner.converse(_plan(), [], "build me a generator", "discovery"))

    assert reply.message == ""
    assert reply.intent is None
    assert len(client.calls) == 1


def test_an_empty_completion_where_a_submission_is_required_fails_loudly() -> None:
    """The path that matters: architecture has no plain-reply escape, so a model
    returning nothing must surface as a failure rather than as an empty draft."""
    reasoner = OpenAIReasoner(SilentLLMClient(), CAPS, converse_max_turns=3)

    with pytest.raises(Exception):
        asyncio.run(reasoner.architect_cycle(_approved_plan()))
