"""OpenAIReasoner on the FakeLLMClient: ask vs commit turns, history replay as
plain text, goal/task building with ids+positions, unknown-capability
self-correction and the final filtered accept."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from src.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from src.domain.entities.capability import Capability
from src.domain.entities.goal import Goal
from src.domain.entities.planning_artifacts import (
    Cycle,
    CycleStatus,
    IntentProposal,
    ProposalKind,
)
from src.domain.ports.reasoner_port import ChatMessage
from src.infra.reasoner.openai_reasoner import OpenAIReasoner
from tests.fakes_llm import FakeLLMClient, text_turn, tool_turn

T0 = datetime(2026, 7, 3, tzinfo=timezone.utc)
CAPS = [Capability(id="backend", name="Backend", description="")]


def make_plan(phase=PlanPhase.DISCOVERY):
    return Plan(project_id="project-1", id="p1", brief="tiny service", phase=phase)


def msg(role, content):
    return ChatMessage(role=role, content=content, created_at=T0)


def converse(reasoner, plan, history, message, mode="discovery"):
    return asyncio.run(reasoner.converse(plan, history, message, mode))


def test_plain_text_reply_is_the_question_turn():
    client = FakeLLMClient([text_turn("Monolith or microservices?")])
    reply = converse(OpenAIReasoner(client, CAPS), make_plan(), [], "plan me an app")

    assert reply.goals is None
    assert reply.message == "Monolith or microservices?"
    # transcript shape: system, phase prompt, current user message
    roles = [m["role"] for m in client.calls[0]["messages"]]
    assert roles == ["system", "user", "user"]


def test_history_replays_as_plain_text_turns():
    client = FakeLLMClient([text_turn("noted")])
    history = [msg("user", "hello"), msg("assistant", "which db?")]
    converse(OpenAIReasoner(client, CAPS), make_plan(), history, "sqlite")

    sent = client.calls[0]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "user", "assistant", "user"]
    assert sent[2] == {"role": "user", "content": "hello"}
    assert sent[3] == {"role": "assistant", "content": "which db?"}
    assert sent[4] == {"role": "user", "content": "sqlite"}


def intent_args(**overrides):
    values = {
        "normalized_brief": "Build a small API service.",
        "objective": "Ship a maintainable API service.",
        "scope": ["HTTP API"],
        "constraints": ["SQLite"],
        "exclusions": ["mobile client"],
        "assumptions": ["single tenant"],
        "unresolved_questions": [],
    }
    values.update(overrides)
    return values


def test_submit_intent_returns_normalized_review_candidate():
    client = FakeLLMClient(
        [
            tool_turn(
                "submit_intent_proposal",
                intent_args(),
            )
        ]
    )
    reply = converse(OpenAIReasoner(client, CAPS), make_plan(), [], "go")

    assert reply.goals is None
    assert reply.intent is not None
    assert reply.intent.normalized_brief == "Build a small API service."
    assert reply.intent.constraints == ["SQLite"]
    assert reply.intent.assumptions == ["single tenant"]
    assert client.calls[0]["tool_names"] == [
        "read_project_spec",
        "read_project_plan",
        "read_repository_context",
        "read_conversation",
        "submit_intent_proposal",
    ]


def test_submitted_intent_cannot_retain_unresolved_questions():
    client = FakeLLMClient(
        [
            tool_turn(
                "submit_intent_proposal",
                intent_args(unresolved_questions=["Which region?"]),
            ),
        ]
    )
    with pytest.raises(ValueError, match="unresolved questions"):
        converse(OpenAIReasoner(client, CAPS), make_plan(), [], "go")


def test_enrich_goal_builds_ordered_tasks():
    client = FakeLLMClient(
        [
            tool_turn(
                "submit_tasks",
                {
                    "tasks": [
                        {
                            "name": "write models",
                            "description": "d1",
                            "required_capabilities": ["backend"],
                        },
                        {"name": "wire routes", "description": "d2"},
                    ]
                },
            )
        ]
    )
    goal = Goal(id="g1", name="API", position=0, description="", tasks=[])
    tasks = asyncio.run(OpenAIReasoner(client, CAPS).enrich_goal(make_plan(), goal, CAPS))

    assert [(t.name, t.position) for t in tasks] == [
        ("write models", 0),
        ("wire routes", 1),
    ]
    assert tasks[0].required_capabilities == ["backend"]
    assert all(t.id for t in tasks)
    # the prompt carried the capability catalog
    prompt = client.calls[0]["messages"][1]["content"]
    assert "`backend`" in prompt and "API" in prompt


def test_unknown_capability_rejected_then_filtered_after_budget():
    bad_submit = {
        "tasks": [
            {"name": "t", "description": "d", "required_capabilities": ["backend", "made-up"]}
        ]
    }
    client = FakeLLMClient(
        [
            tool_turn("submit_tasks", bad_submit, "c1"),
            tool_turn("submit_tasks", bad_submit, "c2"),
            tool_turn("submit_tasks", bad_submit, "c3"),
        ]
    )
    goal = Goal(id="g1", name="API", position=0, description="", tasks=[])
    tasks = asyncio.run(OpenAIReasoner(client, CAPS).enrich_goal(make_plan(), goal, CAPS))

    # two rejections with the unknown id named, then the filtered accept
    assert len(client.calls) == 3
    first_rejection = json.loads(
        next(m["content"] for m in client.calls[1]["messages"] if m.get("role") == "tool")
    )
    assert first_rejection["accepted"] is False
    assert "made-up" in first_rejection["errors"][0]
    (task,) = tasks
    assert task.required_capabilities == ["backend"]  # unknown id filtered


def test_cycle_architecture_uses_only_architecture_profile_tools():
    plan = make_plan()
    plan.intent_proposal = IntentProposal(
        id="intent-1",
        kind=ProposalKind.INITIAL,
        base_plan_version=0,
        objective="ship",
        approved_at=T0,
    )
    client = FakeLLMClient(
        [
            tool_turn(
                "submit_cycle_draft",
                {
                    "goals": [
                        {
                            "key": "delivery",
                            "name": "Delivery",
                            "objective": "ship",
                            "position": 0,
                            "depends_on": [],
                        }
                    ]
                },
            )
        ]
    )
    outlines = asyncio.run(OpenAIReasoner(client, CAPS).architect_cycle(plan))

    assert [item.key for item in outlines] == ["delivery"]
    assert client.calls[0]["tool_names"] == [
        "read_project_spec",
        "read_project_plan",
        "read_repository_context",
        "read_approved_intent",
        "read_prior_evidence",
        "submit_cycle_draft",
    ]
    assert "submit_intent_proposal" not in client.calls[0]["tool_names"]
    assert "submit_goal_contract" not in client.calls[0]["tool_names"]


def test_replan_cycle_architecture_requires_source_plan_accounting():
    plan = make_plan()
    plan.intent_proposal = IntentProposal(
        id="intent-2",
        kind=ProposalKind.REPLAN,
        base_plan_version=0,
        source_cycle_id="cycle-source",
        objective="retry only the failed migration",
        approved_at=T0,
    )
    client = FakeLLMClient(
        [
            tool_turn(
                "submit_cycle_draft",
                {
                    "goals": [
                        {
                            "key": "migration-retry",
                            "name": "Retry migration",
                            "objective": "retry only failed work",
                            "position": 0,
                            "depends_on": [],
                        }
                    ]
                },
            )
        ]
    )

    asyncio.run(OpenAIReasoner(client, CAPS).architect_cycle(plan))

    prompt = client.calls[0]["messages"][1]["content"]
    assert "This is a replan" in prompt
    assert "Read the project plan and prior evidence" in prompt
    assert "do not recreate or redo" in prompt


def test_goal_enrichment_uses_only_contract_profile_tools():
    plan = make_plan(PlanPhase.RUNNING)
    goal = Goal(id="g1", name="API", position=0, description="ship API")
    plan.cycles = [
        Cycle(
            id="cycle-1",
            intent_proposal_id="intent-1",
            draft_id="draft-1",
            status=CycleStatus.ACTIVE,
            goals=[goal],
            started_at=T0,
        )
    ]
    client = FakeLLMClient(
        [
            tool_turn(
                "submit_goal_contract",
                {
                    "objective": "ship API",
                    "acceptance_criteria": [{"id": "g-1", "description": "API works"}],
                    "tasks": [
                        {
                            "objective": "build API",
                            "acceptance_criteria": [{"id": "t-1", "description": "endpoint works"}],
                            "goal_criterion_ids": ["g-1"],
                            "allowed_scope": ["backend/"],
                            "forbidden_scope": ["frontend/"],
                            "verification_commands": ["pytest -q"],
                            "verification_strategy": "tdd",
                            "required_capabilities": ["backend"],
                        }
                    ],
                    "cross_task_integration_criterion_ids": [],
                    "required_capabilities": ["backend"],
                },
            )
        ]
    )
    contract = asyncio.run(OpenAIReasoner(client, CAPS).enrich_goal_contract(plan, goal, CAPS))

    assert contract.tasks[0].verification_strategy.value == "tdd"
    assert client.calls[0]["tool_names"] == [
        "read_project_spec",
        "read_project_plan",
        "read_repository_context",
        "read_approved_intent",
        "read_active_goal",
        "read_prior_evidence",
        "submit_goal_contract",
    ]
    assert "submit_cycle_draft" not in client.calls[0]["tool_names"]


def test_tdd_step_submission_is_rejected_then_feature_slice_is_accepted():
    bad = {
        "tasks": [
            {
                "name": "Write failing tests for Item",
                "description": "Add red tests",
                "verification_strategy": "tdd",
            }
        ]
    }
    good = {
        "tasks": [
            {
                "name": "Deliver validated Item schemas",
                "description": "Implement Item schemas and their passing tests",
                "verification_strategy": "tdd",
            }
        ]
    }
    client = FakeLLMClient(
        [
            tool_turn("submit_tasks", bad, "bad"),
            tool_turn("submit_tasks", good, "good"),
        ]
    )
    goal = Goal(id="g1", name="API", position=0, description="", tasks=[])

    tasks = asyncio.run(OpenAIReasoner(client, CAPS).enrich_goal(make_plan(), goal, CAPS))

    rejection = json.loads(
        next(m["content"] for m in client.calls[1]["messages"] if m.get("role") == "tool")
    )
    assert rejection["accepted"] is False
    assert "feature-level deliverable slices" in rejection["errors"][0]
    assert [task.name for task in tasks] == ["Deliver validated Item schemas"]


def test_enrich_prompt_states_tdd_granularity_rule():
    client = FakeLLMClient(
        [tool_turn("submit_tasks", {"tasks": [{"name": "deliver feature", "description": "d"}]})]
    )
    goal = Goal(id="g1", name="API", position=0, description="", tasks=[])

    asyncio.run(OpenAIReasoner(client, CAPS).enrich_goal(make_plan(), goal, CAPS))

    prompt = client.calls[0]["messages"][1]["content"]
    assert "feature-level deliverable slice" in prompt
    assert "red/green split internally" in prompt


def test_goal_contract_tdd_step_submission_is_rejected_then_accepted():
    def contract_task(objective: str) -> dict[str, object]:
        return {
            "objective": objective,
            "acceptance_criteria": [{"id": "t-1", "description": "Item works"}],
            "goal_criterion_ids": ["g-1"],
            "allowed_scope": ["backend/"],
            "verification_commands": ["pytest -q"],
            "verification_strategy": "tdd",
        }

    bad = {
        "objective": "Build Item",
        "acceptance_criteria": [{"id": "g-1", "description": "Item works"}],
        "tasks": [contract_task("Write tests for Item")],
    }
    good = {**bad, "tasks": [contract_task("Deliver validated Item schemas with passing tests")]}
    client = FakeLLMClient(
        [
            tool_turn("submit_goal_contract", bad, "bad"),
            tool_turn("submit_goal_contract", good, "good"),
        ]
    )
    plan = make_plan(PlanPhase.RUNNING)
    goal = Goal(id="g1", name="API", position=0, description="", tasks=[])

    contract = asyncio.run(OpenAIReasoner(client, CAPS).enrich_goal_contract(plan, goal, CAPS))

    rejection = json.loads(
        next(m["content"] for m in client.calls[1]["messages"] if m.get("role") == "tool")
    )
    assert rejection["accepted"] is False
    assert "feature-level deliverable slices" in rejection["errors"][0]
    assert contract.tasks[0].objective == "Deliver validated Item schemas with passing tests"


# ---- runtime-neutral model usage observations ----
def test_converse_records_reported_usage_with_provenance():
    from src.app.observations import ObservationQuality, ObservationSource
    from src.app.testing.observations import InMemoryObservationRepository

    client = FakeLLMClient(
        [
            tool_turn(
                "read_conversation",
                {},
                "c1",
                usage={
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            ),
            tool_turn(
                "submit_intent_proposal",
                intent_args(),
                "c2",
                usage={"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
            ),
        ]
    )
    repository = InMemoryObservationRepository(lambda: T0)
    reasoner = OpenAIReasoner(
        client,
        CAPS,
        observation_repository=repository,
        provider="provider-x",
    )

    # one read-tool turn then an accepted submit: 2 model calls, usage summed
    reply = converse(reasoner, make_plan(), [], "go")
    assert reply.intent is not None

    (stored,) = repository.observations
    observation = stored.observation
    assert observation.correlation.plan_id == "p1"
    assert observation.correlation.task_id is None
    assert observation.source is ObservationSource.PROVIDER
    assert observation.quality is ObservationQuality.REPORTED
    assert observation.payload.context == "discovery"
    assert observation.payload.model_request_count == 2
    assert observation.payload.input_tokens == 30
    assert observation.payload.output_tokens == 13
    assert observation.payload.total_tokens == 43
    assert observation.payload.provider == "provider-x"


def test_enrich_records_missing_usage_as_unavailable_not_zero():
    from src.app.observations import ObservationQuality
    from src.app.testing.observations import InMemoryObservationRepository

    client = FakeLLMClient(
        [tool_turn("submit_tasks", {"tasks": [{"name": "t", "description": "d"}]})]
    )
    repository = InMemoryObservationRepository(lambda: T0)
    goal = Goal(id="g1", name="API", position=0, description="", tasks=[])
    asyncio.run(
        OpenAIReasoner(
            client,
            CAPS,
            observation_repository=repository,
        ).enrich_goal(make_plan(PlanPhase.ENRICHING), goal, CAPS)
    )

    (stored,) = repository.observations
    observation = stored.observation
    assert observation.quality is ObservationQuality.UNAVAILABLE
    assert observation.payload.context == "enrich"
    assert observation.payload.input_tokens is None
    assert observation.payload.output_tokens is None
    assert observation.payload.total_tokens is None
    assert observation.payload.unavailable_reason == "provider_did_not_report_usage"


def test_observation_failure_does_not_fail_reasoning():
    class FailingRepository:
        async def append(self, observation):
            raise RuntimeError("telemetry unavailable")

        def get(self, observation_id):
            raise KeyError(observation_id)

    client = FakeLLMClient([text_turn("hi")])
    reply = converse(
        OpenAIReasoner(client, CAPS, observation_repository=FailingRepository()),
        make_plan(),
        [],
        "go",
    )
    assert reply.message == "hi"


def test_no_observation_repository_is_a_silent_noop():
    client = FakeLLMClient([text_turn("hi")])
    reply = converse(OpenAIReasoner(client, CAPS), make_plan(), [], "go")
    assert reply.message == "hi"
