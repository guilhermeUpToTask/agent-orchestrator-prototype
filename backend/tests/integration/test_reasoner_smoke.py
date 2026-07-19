"""Cost-gated REAL-provider smoke test. Never runs in normal CI: it requires
the `llm` marker AND the REASONER_SMOKE_API_KEY env var. One converse turn
against a genuine OpenAI-compatible endpoint proves the client wiring
(auth, base_url, tool wire shape) end to end.

    REASONER_SMOKE_API_KEY=sk-... \\
    REASONER_SMOKE_BASE_URL=https://openrouter.ai/api/v1 \\
    REASONER_SMOKE_MODEL=openai/gpt-4o-mini \\
    pytest -m llm
"""

from __future__ import annotations

import asyncio
import os

import pytest

from src.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from src.domain.entities.capability import Capability
from src.infra.reasoner.openai_reasoner import OpenAIReasoner
from src.infra.reasoner.runtime.llm_client import OpenAIChatClient

pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(
        not os.environ.get("REASONER_SMOKE_API_KEY"),
        reason="REASONER_SMOKE_API_KEY not set (cost-gated smoke test)",
    ),
]


def test_one_real_converse_turn():
    client = OpenAIChatClient(
        api_key=os.environ["REASONER_SMOKE_API_KEY"],
        model=os.environ.get("REASONER_SMOKE_MODEL", "gpt-4o-mini"),
        base_url=os.environ.get("REASONER_SMOKE_BASE_URL") or None,
    )
    reasoner = OpenAIReasoner(client, [Capability(id="backend", name="Backend", description="")])
    plan = Plan(
        project_id="project-1",
        id="smoke",
        brief="a tiny hello-world API",
        phase=PlanPhase.DISCOVERY,
    )

    reply = asyncio.run(
        reasoner.converse(
            plan,
            [],
            "Commit a one-goal roadmap for this brief now; do not ask questions.",
            "discovery",
        )
    )

    assert reply.message
    # a capable model commits here; a chatty one may still ask — both are
    # valid protocol outcomes, the wiring is what this test proves
    if reply.goals is not None:
        assert len(reply.goals) >= 1
        assert all(g.id and g.name for g in reply.goals)
