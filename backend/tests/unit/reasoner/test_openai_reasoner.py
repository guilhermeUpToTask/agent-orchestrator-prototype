"""OpenAIReasoner on the FakeLLMClient: ask vs commit turns, history replay as
plain text, goal/task building with ids+positions, unknown-capability
self-correction and the final filtered accept."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone


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


def test_schema_invalid_submission_raises_reasoner_unavailable_not_a_crash():
    """Found via a real walkthrough against a free-tier model
    (nvidia/nemotron-3-ultra-550b-a55b:free): the model submitted
    constraints/exclusions/assumptions/unresolved_questions as markdown
    bullet-list STRINGS instead of the schema's list[str] -- a value that
    parses as valid JSON and satisfies `run_tool_session`'s own required-arg
    check, but fails IntentCandidate's stricter Pydantic type, at which
    point it used to escape as an unhandled ValidationError all the way to
    an API 500. It must instead surface as the same ReasonerUnavailable
    every other reasoner failure does, so PlanningHandler's existing
    backoff/block machinery handles it."""
    import pytest

    from src.app.ports import ReasonerUnavailable
    from src.domain.value_objects.lifecycle import FailureKind

    client = FakeLLMClient(
        [
            tool_turn(
                "submit_intent_proposal",
                intent_args(constraints="- one\n- two", exclusions="- three"),
            )
        ]
    )

    with pytest.raises(ReasonerUnavailable) as excinfo:
        converse(OpenAIReasoner(client, CAPS), make_plan(), [], "go")

    assert excinfo.value.transient is True
    assert excinfo.value.kind is FailureKind.TOOL_ERROR


def test_submitted_intent_with_unresolved_questions_remains_a_question_turn():
    client = FakeLLMClient(
        [
            tool_turn(
                "submit_intent_proposal",
                intent_args(unresolved_questions=["Which region?"]),
            ),
        ]
    )
    reply = converse(OpenAIReasoner(client, CAPS), make_plan(), [], "go")

    assert reply.intent is None
    assert reply.message == "Which region?"
    assert reply.model_request_count == 1
    assert reply.tool_turn_count == 1


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
                            "allowed_scope": ["backend/", "tests/"],
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


def test_read_project_plan_serves_the_bounded_rendering_not_the_raw_aggregate():
    """`read_project_plan` used to return `plan.model_dump_json()` — the whole
    aggregate, including every cycle, task contract, test bundle and evidence
    record, plus operational fields the planner has no business seeing
    (`retry_policy`, `goal_promotion_reservations`, lease state).

    `render_plan_context` exists for exactly this and was already used by the
    conversational prompts; the cyclic transforms bypassed it. The raw dump
    buries `plan.brief` in noise and burns the turn the model needed to submit —
    observed live, where enrichment spent its whole budget on reads.
    """
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
            tool_turn("read_project_plan", {}, "r1"),
            tool_turn(
                "submit_goal_contract",
                {
                    "objective": "ship API",
                    "acceptance_criteria": [{"id": "g-1", "description": "API works"}],
                    "tasks": [_coverage_task(["g-1"])],
                },
                "s1",
            ),
        ]
    )

    asyncio.run(OpenAIReasoner(client, CAPS).enrich_goal_contract(plan, goal, CAPS))

    served = next(m["content"] for m in client.calls[1]["messages"] if m.get("role") == "tool")
    assert "## Plan" in served and plan.brief in served  # the rendering, brief up front
    assert "retry_policy" not in served  # not the raw aggregate
    assert "goal_promotion_reservations" not in served


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
            "allowed_scope": ["backend/", "tests/"],
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


def test_goal_contract_unknown_capability_rejected_then_filtered_after_budget():
    """`submit_goal_contract` must validate required_capabilities against the
    catalog, exactly as the legacy `submit_tasks` path already does.

    Observed live: a real reasoner invented 'file_operations' and 'python',
    nothing rejected them, they were frozen into the TaskContract, and the
    plan hard-blocked at goal_enrichment with
    `agent_capability: no configured agent covers test_author:
    ['file_operations', 'python', 'test_authoring']`. The capability guard
    existed only on the quarantined legacy transform and was never carried
    forward to the cyclic lifecycle, so the CURRENT path was less robust than
    the legacy one it replaced.
    """

    def contract_task(caps: list[str]) -> dict[str, object]:
        return {
            "objective": "Deliver validated Item schemas with passing tests",
            "acceptance_criteria": [{"id": "t-1", "description": "Item works"}],
            "goal_criterion_ids": ["g-1"],
            "allowed_scope": ["backend/", "tests/"],
            "verification_commands": ["pytest -q"],
            "verification_strategy": "tdd",
            "required_capabilities": caps,
        }

    invented = {
        "objective": "Build Item",
        "acceptance_criteria": [{"id": "g-1", "description": "Item works"}],
        "tasks": [contract_task(["file_operations", "python"])],
    }
    corrected = {**invented, "tasks": [contract_task(["backend"])]}
    client = FakeLLMClient(
        [
            tool_turn("submit_goal_contract", invented, "bad"),
            tool_turn("submit_goal_contract", corrected, "good"),
        ]
    )
    plan = make_plan(PlanPhase.RUNNING)
    goal = Goal(id="g1", name="API", position=0, description="", tasks=[])

    contract = asyncio.run(OpenAIReasoner(client, CAPS).enrich_goal_contract(plan, goal, CAPS))

    rejection = json.loads(
        next(m["content"] for m in client.calls[1]["messages"] if m.get("role") == "tool")
    )
    assert rejection["accepted"] is False
    assert "unknown capability id" in rejection["errors"][0]
    assert "file_operations" in rejection["errors"][0]
    # The corrected submission survives intact.
    assert contract.tasks[0].required_capabilities == ["backend"]


def test_goal_contract_filters_unknown_capabilities_once_the_budget_is_spent():
    """A model that will not correct itself must never freeze an unsatisfiable
    contract: past the rejection budget the unknown ids are dropped, so the
    goal degrades to a resolvable role instead of hard-blocking the plan."""

    def contract_task() -> dict[str, object]:
        return {
            "objective": "Deliver validated Item schemas with passing tests",
            "acceptance_criteria": [{"id": "t-1", "description": "Item works"}],
            "goal_criterion_ids": ["g-1"],
            "allowed_scope": ["backend/", "tests/"],
            "verification_commands": ["pytest -q"],
            "verification_strategy": "tdd",
            "required_capabilities": ["python", "backend"],
        }

    stubborn = {
        "objective": "Build Item",
        "acceptance_criteria": [{"id": "g-1", "description": "Item works"}],
        "tasks": [contract_task()],
    }
    client = FakeLLMClient(
        [tool_turn("submit_goal_contract", stubborn, f"c{i}") for i in range(12)]
    )
    plan = make_plan(PlanPhase.RUNNING)
    goal = Goal(id="g1", name="API", position=0, description="", tasks=[])

    contract = asyncio.run(OpenAIReasoner(client, CAPS).enrich_goal_contract(plan, goal, CAPS))

    assert contract.tasks[0].required_capabilities == ["backend"], (
        "unknown ids must be filtered on build so role resolution can succeed"
    )


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


def _coverage_task(goal_ids: list[str], objective: str = "Deliver it") -> dict[str, object]:
    return {
        "objective": objective,
        "acceptance_criteria": [{"id": "t-1", "description": "works"}],
        "goal_criterion_ids": goal_ids,
        # a tdd contract must leave the test-authoring stage somewhere legal to
        # write, or no agent can satisfy it
        "allowed_scope": ["backend/", "tests/"],
        "verification_commands": ["pytest -q"],
        "verification_strategy": "tdd",
        "required_capabilities": ["backend"],
    }


def test_uncovered_goal_criterion_is_rejected_in_session_not_after_it():
    """Observed live against nvidia/nemotron-3-ultra-550b-a55b:free: the model left
    one acceptance criterion unmapped, GoalContract's model_validator rejected it
    AFTER the session had ended, the submission was discarded, the model was never
    told what was wrong, and the next planning attempt repeated the mistake until the
    attempt budget opened a reasoner_failure block on the goal.

    The rule must be enforced inside the session so the model can repair it.
    """
    uncovered = {
        "objective": "Build Item",
        "acceptance_criteria": [
            {"id": "g-1", "description": "Item works"},
            {"id": "ac-tests-pass", "description": "tests pass"},
        ],
        "tasks": [_coverage_task(["g-1"])],  # 'ac-tests-pass' covered by nothing
    }
    corrected = {**uncovered, "tasks": [_coverage_task(["g-1", "ac-tests-pass"])]}
    client = FakeLLMClient(
        [
            tool_turn("submit_goal_contract", uncovered, "bad"),
            tool_turn("submit_goal_contract", corrected, "good"),
        ]
    )
    plan = make_plan(PlanPhase.RUNNING)
    goal = Goal(id="g1", name="API", position=0, description="", tasks=[])

    contract = asyncio.run(OpenAIReasoner(client, CAPS).enrich_goal_contract(plan, goal, CAPS))

    rejection = json.loads(
        next(m["content"] for m in client.calls[1]["messages"] if m.get("role") == "tool")
    )
    assert rejection["accepted"] is False
    # the error must NAME the offending id, or the model cannot act on it
    assert any("ac-tests-pass" in err for err in rejection["errors"])
    assert contract.tasks[0].goal_criterion_ids == ["g-1", "ac-tests-pass"]


def test_cross_task_integration_criterion_missing_from_final_task_is_repairable():
    """The other live failure. `cross_task_integration_criterion_ids` is optional and
    defaults to empty, so populating it — which the field name invites — switches on
    an extra constraint: every id listed must ALSO appear in the LAST task. A model
    being thorough was punished for it, with no feedback."""
    bad = {
        "objective": "Build Item",
        "acceptance_criteria": [
            {"id": "g-1", "description": "unit works"},
            {"id": "g-2", "description": "integrated"},
        ],
        "tasks": [
            _coverage_task(["g-1"], "unit work"),
            _coverage_task(["g-2"], "more unit work"),
        ],
        # declared as cross-task but absent from the final task's ids
        "cross_task_integration_criterion_ids": ["g-1"],
    }
    good = {**bad, "cross_task_integration_criterion_ids": []}
    client = FakeLLMClient(
        [
            tool_turn("submit_goal_contract", bad, "bad"),
            tool_turn("submit_goal_contract", good, "good"),
        ]
    )
    plan = make_plan(PlanPhase.RUNNING)
    goal = Goal(id="g1", name="API", position=0, description="", tasks=[])

    asyncio.run(OpenAIReasoner(client, CAPS).enrich_goal_contract(plan, goal, CAPS))

    rejection = json.loads(
        next(m["content"] for m in client.calls[1]["messages"] if m.get("role") == "tool")
    )
    assert rejection["accepted"] is False
    err = " ".join(rejection["errors"])
    assert "LAST task" in err and "g-1" in err
    # and it must say that leaving the optional field empty is a valid way out
    assert "empty" in err


def test_capability_id_used_as_allowed_scope_is_rejected_in_session():
    """Observed live against nvidia/nemotron-3-super-120b-a12b:free: the model filled
    `allowed_scope` with the CAPABILITY ids it had just put in `required_capabilities`
    (`["implementation"]`, `["test_authoring"]`) instead of repository-relative paths.

    Nothing caught it. `allowed_scope` had no schema description and no validation, so
    the contract froze with a scope no file can ever match: `_matches_scope` compares
    path prefixes, so EVERY changed file came back `path outside allowed scope`, both
    policy attempts failed, and the goal blocked with execution_failure. A real agent
    CLI editing the right file would have been rejected identically.
    """
    caps = [
        Capability(id="backend", name="Backend", description=""),
        Capability(id="implementation", name="Implementation", description=""),
    ]
    bad_task = {**_coverage_task(["g-1"]), "allowed_scope": ["implementation", "tests/"]}
    bad = {
        "objective": "Build Item",
        "acceptance_criteria": [{"id": "g-1", "description": "works"}],
        "tasks": [bad_task],
    }
    good = {
        **bad,
        "tasks": [{**bad_task, "allowed_scope": ["src/happy_path/greeter.py", "tests/"]}],
    }
    client = FakeLLMClient(
        [
            tool_turn("submit_goal_contract", bad, "bad"),
            tool_turn("submit_goal_contract", good, "good"),
        ]
    )
    plan = make_plan(PlanPhase.RUNNING)
    goal = Goal(id="g1", name="API", position=0, description="", tasks=[])

    contract = asyncio.run(OpenAIReasoner(client, caps).enrich_goal_contract(plan, goal, caps))

    rejection = json.loads(
        next(m["content"] for m in client.calls[1]["messages"] if m.get("role") == "tool")
    )
    assert rejection["accepted"] is False
    err = " ".join(rejection["errors"])
    # name the offending entry, say what the field means, and give the escape hatch
    assert "implementation" in err and "allowed_scope" in err
    assert "path" in err and "required_capabilities" in err
    assert contract.tasks[0].allowed_scope == ["src/happy_path/greeter.py", "tests/"]


def test_scope_entry_that_is_really_a_directory_survives_with_a_trailing_slash():
    """The rejection keys off capability ids, so a project that genuinely has a
    directory named after one must stay expressible: a trailing slash marks the entry
    as a path and is accepted. Without this the check would be unsatisfiable."""
    caps = [
        Capability(id="backend", name="Backend", description=""),
        Capability(id="implementation", name="Implementation", description=""),
    ]
    task = {**_coverage_task(["g-1"]), "allowed_scope": ["implementation/", "tests/"]}
    args = {
        "objective": "Build Item",
        "acceptance_criteria": [{"id": "g-1", "description": "works"}],
        "tasks": [task],
    }
    client = FakeLLMClient([tool_turn("submit_goal_contract", args, "ok")])
    plan = make_plan(PlanPhase.RUNNING)
    goal = Goal(id="g1", name="API", position=0, description="", tasks=[])

    contract = asyncio.run(OpenAIReasoner(client, caps).enrich_goal_contract(plan, goal, caps))

    assert contract.tasks[0].allowed_scope == ["implementation/", "tests/"]
    assert len(client.calls) == 1  # accepted on the first submission, no repair turn


def test_enrichment_schema_states_what_allowed_scope_means():
    """The field had no description at all, which is why the model had to guess."""
    from src.infra.reasoner.openai_reasoner import SUBMIT_GOAL_CONTRACT_SCHEMA

    task_props = SUBMIT_GOAL_CONTRACT_SCHEMA["properties"]["tasks"]["items"]["properties"]
    described = task_props["allowed_scope"]["description"]
    assert "path" in described
    assert "capabilit" in described  # must warn against the observed confusion


class FakeRepositoryReader:
    """A tiny in-memory `RepositoryReader`. The seed layout is the fixture's:
    the test file is `tests/test_greeter.py`, and the live failure was a
    contract naming `tests/test_greet.py`."""

    def __init__(self, paths: list[str] | None = None, *, fails: bool = False) -> None:
        self.paths = paths if paths is not None else [
            "pyproject.toml",
            "src/happy_path/greeter.py",
            "tests/test_greeter.py",
        ]
        self.fails = fails

    def _guard(self):
        if self.fails:
            raise RuntimeError("cannot resolve project")

    def orientation(self, project_id):
        self._guard()
        from src.app.ports import RepositoryOrientation

        return RepositoryOrientation(
            default_branch="main",
            top_level_entries=("pyproject.toml", "src", "tests"),
            test_directories=("tests",),
            detected_test_command="python -m pytest -q",
            config_files=("pyproject.toml",),
        )

    def list_paths(self, project_id, *, prefix="", max_entries=200):
        self._guard()
        return [p for p in self.paths if p.startswith(prefix)][:max_entries]

    def read_file(self, project_id, path, *, max_bytes=20_000):
        self._guard()
        return "def greet(name): ...\n"

    def search(self, project_id, pattern, *, path_prefix="", max_hits=50):
        self._guard()
        return []

    def exists(self, project_id, path):
        self._guard()
        return path in self.paths


def _enrichment_plan_and_goal():
    plan = make_plan(PlanPhase.RUNNING)
    goal = Goal(id="g1", name="Implement greet", position=0, description="return a greeting")
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
    return plan, goal


def test_a_verification_command_naming_a_missing_file_is_rejected_with_the_near_miss():
    """The live failure, in one test. The model wrote
    `pytest -q tests/test_greet.py` for a repository whose file is
    `tests/test_greeter.py`; nothing checked, the contract froze, and every
    attempt then failed against a command that could never pass.

    The rejection must NAME the near miss — an error the model cannot act on
    just burns the repair turn.
    """
    plan, goal = _enrichment_plan_and_goal()
    bad_task = {
        **_coverage_task(["g-1"]),
        "allowed_scope": ["src/happy_path/greeter.py", "tests/"],
        "verification_commands": ["pytest -q tests/test_greet.py"],
    }
    bad = {
        "objective": "Implement greet",
        "acceptance_criteria": [{"id": "g-1", "description": "greets"}],
        "tasks": [bad_task],
    }
    good = {
        **bad,
        "tasks": [{**bad_task, "verification_commands": ["pytest -q tests/test_greeter.py"]}],
    }
    client = FakeLLMClient(
        [
            tool_turn("submit_goal_contract", bad, "bad"),
            tool_turn("submit_goal_contract", good, "good"),
        ]
    )
    reasoner = OpenAIReasoner(client, CAPS, repository_reader=FakeRepositoryReader())

    contract = asyncio.run(reasoner.enrich_goal_contract(plan, goal, CAPS))

    rejection = json.loads(
        next(m["content"] for m in client.calls[1]["messages"] if m.get("role") == "tool")
    )
    assert rejection["accepted"] is False
    err = " ".join(rejection["errors"])
    assert "tests/test_greet.py" in err and "does not exist" in err
    assert "tests/test_greeter.py" in err  # the near miss, named
    assert contract.tasks[0].verification_commands == ["pytest -q tests/test_greeter.py"]


def test_a_scope_matching_nothing_is_rejected_before_it_can_freeze():
    plan, goal = _enrichment_plan_and_goal()
    bad_task = {**_coverage_task(["g-1"]), "allowed_scope": ["app/services/", "tests/"]}
    bad = {
        "objective": "Implement greet",
        "acceptance_criteria": [{"id": "g-1", "description": "greets"}],
        "tasks": [bad_task],
    }
    good = {**bad, "tasks": [{**bad_task, "allowed_scope": ["src/happy_path/", "tests/"]}]}
    client = FakeLLMClient(
        [
            tool_turn("submit_goal_contract", bad, "bad"),
            tool_turn("submit_goal_contract", good, "good"),
        ]
    )
    reasoner = OpenAIReasoner(client, CAPS, repository_reader=FakeRepositoryReader())

    contract = asyncio.run(reasoner.enrich_goal_contract(plan, goal, CAPS))

    rejection = json.loads(
        next(m["content"] for m in client.calls[1]["messages"] if m.get("role") == "tool")
    )
    err = " ".join(rejection["errors"])
    assert "app/services/" in err and "matches nothing" in err
    assert contract.tasks[0].allowed_scope == ["src/happy_path/", "tests/"]


def test_a_new_file_under_an_existing_directory_is_accepted():
    """A task that CREATES a module is the normal case — the rule is that the
    parent directory exists, not that the file already does. Rejecting this
    would make the check unusable."""
    plan, goal = _enrichment_plan_and_goal()
    task = {**_coverage_task(["g-1"]), "allowed_scope": ["src/happy_path/formatter.py", "tests/"]}
    client = FakeLLMClient(
        [
            tool_turn(
                "submit_goal_contract",
                {
                    "objective": "Implement greet",
                    "acceptance_criteria": [{"id": "g-1", "description": "greets"}],
                    "tasks": [task],
                },
                "ok",
            )
        ]
    )
    reasoner = OpenAIReasoner(client, CAPS, repository_reader=FakeRepositoryReader())

    contract = asyncio.run(reasoner.enrich_goal_contract(plan, goal, CAPS))

    assert contract.tasks[0].allowed_scope == ["src/happy_path/formatter.py", "tests/"]
    assert len(client.calls) == 1  # accepted first time, no repair turn burned


def test_a_command_naming_a_test_file_the_task_will_author_is_accepted():
    """The TDD RED stage authors its own test file, so a verification command
    naming a not-yet-existing path is legitimate. A missing path alone proves
    nothing — only a missing path with a near-twin that exists is a typo."""
    plan, goal = _enrichment_plan_and_goal()
    task = {
        **_coverage_task(["g-1"]),
        "allowed_scope": ["src/happy_path/", "tests/"],
        "verification_commands": ["pytest -q tests/test_formatting_rules.py"],
    }
    client = FakeLLMClient(
        [
            tool_turn(
                "submit_goal_contract",
                {
                    "objective": "Implement greet",
                    "acceptance_criteria": [{"id": "g-1", "description": "greets"}],
                    "tasks": [task],
                },
                "ok",
            )
        ]
    )
    reasoner = OpenAIReasoner(client, CAPS, repository_reader=FakeRepositoryReader())

    contract = asyncio.run(reasoner.enrich_goal_contract(plan, goal, CAPS))

    assert contract.tasks[0].verification_commands == ["pytest -q tests/test_formatting_rules.py"]
    assert len(client.calls) == 1


def test_scope_that_is_both_allowed_and_forbidden_is_rejected():
    plan, goal = _enrichment_plan_and_goal()
    bad_task = {
        **_coverage_task(["g-1"]),
        "allowed_scope": ["src/happy_path/", "tests/"],
        "forbidden_scope": ["src/happy_path/"],
    }
    good_task = {**bad_task, "forbidden_scope": []}
    client = FakeLLMClient(
        [
            tool_turn(
                "submit_goal_contract",
                {
                    "objective": "Implement greet",
                    "acceptance_criteria": [{"id": "g-1", "description": "greets"}],
                    "tasks": [bad_task],
                },
                "bad",
            ),
            tool_turn(
                "submit_goal_contract",
                {
                    "objective": "Implement greet",
                    "acceptance_criteria": [{"id": "g-1", "description": "greets"}],
                    "tasks": [good_task],
                },
                "good",
            ),
        ]
    )
    reasoner = OpenAIReasoner(client, CAPS, repository_reader=FakeRepositoryReader())

    asyncio.run(reasoner.enrich_goal_contract(plan, goal, CAPS))

    rejection = json.loads(
        next(m["content"] for m in client.calls[1]["messages"] if m.get("role") == "tool")
    )
    err = " ".join(rejection["errors"])
    assert "BOTH allowed_scope and forbidden_scope" in err


def test_a_tdd_contract_whose_scope_excludes_tests_is_rejected():
    """The defect that ended a live Tier 1 run. Enrichment froze
    `verification_strategy: tdd` with `allowed_scope: ["src/happy_path/greeter.py"]`
    — no test path at all.

    TDD's first stage authors the failing test and is forbidden from touching
    production files, so the test author had nowhere legal to write: anything in
    tests/ was out of scope, and the one in-scope file was production. Both
    attempts died `test author modified production paths` and the goal blocked.
    NO agent could have satisfied that contract; the strategy and the scope
    contradicted each other at the moment they were frozen.
    """
    plan, goal = _enrichment_plan_and_goal()
    bad_task = {
        **_coverage_task(["g-1"]),
        "allowed_scope": ["src/happy_path/greeter.py"],
        "verification_strategy": "tdd",
    }
    good_task = {
        **bad_task,
        "allowed_scope": ["src/happy_path/greeter.py", "tests/test_greeter.py"],
    }
    body = {
        "objective": "Implement greet",
        "acceptance_criteria": [{"id": "g-1", "description": "greets"}],
    }
    client = FakeLLMClient(
        [
            tool_turn("submit_goal_contract", {**body, "tasks": [bad_task]}, "bad"),
            tool_turn("submit_goal_contract", {**body, "tasks": [good_task]}, "good"),
        ]
    )
    reasoner = OpenAIReasoner(client, CAPS, repository_reader=FakeRepositoryReader())

    contract = asyncio.run(reasoner.enrich_goal_contract(plan, goal, CAPS))

    rejection = json.loads(
        next(m["content"] for m in client.calls[1]["messages"] if m.get("role") == "tool")
    )
    err = " ".join(rejection["errors"])
    assert "tdd" in err and "test" in err
    assert contract.tasks[0].allowed_scope == [
        "src/happy_path/greeter.py",
        "tests/test_greeter.py",
    ]


def test_an_executable_check_contract_needs_no_test_path():
    """`executable_check` has no RED stage — requiring a test path there would
    reject the very strategy that exists for work where RED is meaningless."""
    plan, goal = _enrichment_plan_and_goal()
    task = {
        **_coverage_task(["g-1"]),
        "allowed_scope": ["src/happy_path/greeter.py"],
        "verification_strategy": "executable_check",
    }
    client = FakeLLMClient(
        [
            tool_turn(
                "submit_goal_contract",
                {
                    "objective": "Implement greet",
                    "acceptance_criteria": [{"id": "g-1", "description": "greets"}],
                    "tasks": [task],
                },
                "ok",
            )
        ]
    )
    reasoner = OpenAIReasoner(client, CAPS, repository_reader=FakeRepositoryReader())

    contract = asyncio.run(reasoner.enrich_goal_contract(plan, goal, CAPS))

    assert contract.tasks[0].allowed_scope == ["src/happy_path/greeter.py"]
    assert len(client.calls) == 1


def test_the_prompt_states_the_goal_and_the_repository_without_a_single_read():
    """`reserved_submit_turns` guarantees a submission but not that the model
    ever read anything. Everything needed to write a correct contract has to be
    in the prompt already; the tools are for depth."""
    plan, goal = _enrichment_plan_and_goal()
    client = FakeLLMClient(
        [
            tool_turn(
                "submit_goal_contract",
                {
                    "objective": "Implement greet",
                    "acceptance_criteria": [{"id": "g-1", "description": "greets"}],
                    "tasks": [_coverage_task(["g-1"])],
                },
                "ok",
            )
        ]
    )
    reasoner = OpenAIReasoner(client, CAPS, repository_reader=FakeRepositoryReader())

    asyncio.run(reasoner.enrich_goal_contract(plan, goal, CAPS))

    prompt = " ".join(
        m["content"] for m in client.calls[0]["messages"] if m.get("role") == "user"
    )
    assert goal.name in prompt and plan.brief in prompt
    assert "tests" in prompt and "python -m pytest -q" in prompt


def test_no_repository_sight_degrades_to_a_warning_instead_of_failing_planning():
    """An unresolvable project must not kill enrichment: no sight is worse than
    sight, but far better than a crash inside a tool callback."""
    plan, goal = _enrichment_plan_and_goal()
    client = FakeLLMClient(
        [
            tool_turn(
                "submit_goal_contract",
                {
                    "objective": "Implement greet",
                    "acceptance_criteria": [{"id": "g-1", "description": "greets"}],
                    # a scope nothing could satisfy — but with no sight there is
                    # no ground truth to judge it against, so it must NOT be
                    # rejected on an invented verdict
                    "tasks": [{**_coverage_task(["g-1"]), "allowed_scope": ["anywhere/", "tests/"]}],
                },
                "ok",
            )
        ]
    )
    reasoner = OpenAIReasoner(client, CAPS, repository_reader=FakeRepositoryReader(fails=True))

    contract = asyncio.run(reasoner.enrich_goal_contract(plan, goal, CAPS))

    assert contract.tasks[0].allowed_scope == ["anywhere/", "tests/"]
    assert "list_repository_paths" not in client.calls[0]["tool_names"]
    prompt = " ".join(m["content"] for m in client.calls[0]["messages"] if m.get("role") == "user")
    assert "UNAVAILABLE" in prompt and "Do not invent file paths" in prompt


def test_task_referencing_an_unknown_criterion_id_is_told_the_valid_ids():
    unknown = {
        "objective": "Build Item",
        "acceptance_criteria": [{"id": "g-1", "description": "works"}],
        "tasks": [_coverage_task(["typo-1"])],
    }
    good = {**unknown, "tasks": [_coverage_task(["g-1"])]}
    client = FakeLLMClient(
        [
            tool_turn("submit_goal_contract", unknown, "bad"),
            tool_turn("submit_goal_contract", good, "good"),
        ]
    )
    plan = make_plan(PlanPhase.RUNNING)
    goal = Goal(id="g1", name="API", position=0, description="", tasks=[])

    asyncio.run(OpenAIReasoner(client, CAPS).enrich_goal_contract(plan, goal, CAPS))

    rejection = json.loads(
        next(m["content"] for m in client.calls[1]["messages"] if m.get("role") == "tool")
    )
    err = " ".join(rejection["errors"])
    assert "typo-1" in err and "g-1" in err  # what was wrong AND what is valid


class FakePriorAttempts:
    def __init__(self, artifacts=None):
        self.artifacts = artifacts or []

    def latest(self, plan_id, purpose, *, goal_id=None, limit=5):
        return [
            a
            for a in self.artifacts
            if a.plan_id == plan_id and a.purpose == purpose and a.goal_id == goal_id
        ][:limit]


def _artifact(fingerprint, *, outcome="rejected", payload=None, reasons=(), sequence=1):
    from src.app.ports import PlanningArtifact

    return PlanningArtifact(
        plan_id="p1",
        goal_id="g1",
        purpose="goal_contract",
        sequence=sequence,
        input_fingerprint=fingerprint,
        outcome=outcome,
        payload=payload,
        rejection_reasons=tuple(reasons),
        created_at=T0,
    )


def _fingerprint_for(plan, goal):
    from src.infra.reasoner.openai_reasoner import _enrichment_fingerprint

    return _enrichment_fingerprint(plan, goal, {c.id for c in CAPS})


def _accepting_client():
    return FakeLLMClient(
        [
            tool_turn(
                "submit_goal_contract",
                {
                    "objective": "Implement greet",
                    "acceptance_criteria": [{"id": "g-1", "description": "greets"}],
                    "tasks": [_coverage_task(["g-1"])],
                },
                "ok",
            )
        ]
    )


def test_a_failed_session_carries_its_rejected_work_out_on_the_exception():
    """The reasoner reads and never persists, so the only way a dead session's
    work survives is attached to the failure. Before this it lived in an
    in-process message list that the raise discarded."""
    import pytest

    from src.app.ports import ReasonerUnavailable

    plan, goal = _enrichment_plan_and_goal()
    bad = {
        "objective": "Implement greet",
        "acceptance_criteria": [{"id": "g-1", "description": "greets"}],
        "tasks": [{**_coverage_task(["g-1"]), "allowed_scope": ["implementation"]}],
    }
    # rejected, then the budget runs out
    client = FakeLLMClient([tool_turn("submit_goal_contract", bad, "bad")])
    reasoner = OpenAIReasoner(
        client, CAPS, enrich_max_turns=1, repository_reader=FakeRepositoryReader()
    )

    with pytest.raises(ReasonerUnavailable) as excinfo:
        asyncio.run(reasoner.enrich_goal_contract(plan, goal, CAPS))

    assert excinfo.value.partial_artifact == bad
    assert any("implementation" in r for r in excinfo.value.rejection_reasons)
    assert excinfo.value.input_fingerprint == _fingerprint_for(plan, goal)


def test_a_prior_rejection_is_replayed_into_the_next_session():
    plan, goal = _enrichment_plan_and_goal()
    fp = _fingerprint_for(plan, goal)
    client = _accepting_client()
    reasoner = OpenAIReasoner(
        client,
        CAPS,
        repository_reader=FakeRepositoryReader(),
        prior_attempts=FakePriorAttempts(
            [
                _artifact(
                    fp,
                    payload={"tasks": [{"allowed_scope": ["implementation"]}]},
                    reasons=("allowed_scope: 'implementation' is a capability id, not a path",),
                )
            ]
        ),
    )

    asyncio.run(reasoner.enrich_goal_contract(plan, goal, CAPS))

    prompt = " ".join(m["content"] for m in client.calls[0]["messages"] if m.get("role") == "user")
    assert "Previous attempts at this goal contract" in prompt
    assert "capability id, not a path" in prompt
    assert "REJECTED" in prompt  # never offered as a starting point


def test_a_replay_from_different_inputs_is_discarded_silently():
    """The hard gate. After an edit or a replan the prior attempt describes work
    the model is no longer being asked to do — worse than useless."""
    plan, goal = _enrichment_plan_and_goal()
    client = _accepting_client()
    reasoner = OpenAIReasoner(
        client,
        CAPS,
        repository_reader=FakeRepositoryReader(),
        prior_attempts=FakePriorAttempts(
            [_artifact("a-stale-fingerprint", reasons=("something about an older goal",))]
        ),
    )

    asyncio.run(reasoner.enrich_goal_contract(plan, goal, CAPS))

    prompt = " ".join(m["content"] for m in client.calls[0]["messages"] if m.get("role") == "user")
    assert "Previous attempts" not in prompt
    assert "older goal" not in prompt


def test_an_abandoned_attempt_is_never_replayed_as_a_rejection():
    """It was dropped by an orchestration race, not refused. Its payload may
    have been perfectly good; labelling it REJECTED teaches a lie."""
    plan, goal = _enrichment_plan_and_goal()
    fp = _fingerprint_for(plan, goal)
    client = _accepting_client()
    reasoner = OpenAIReasoner(
        client,
        CAPS,
        repository_reader=FakeRepositoryReader(),
        prior_attempts=FakePriorAttempts(
            [_artifact(fp, outcome="abandoned", payload={"tasks": []}, reasons=())]
        ),
    )

    asyncio.run(reasoner.enrich_goal_contract(plan, goal, CAPS))

    prompt = " ".join(m["content"] for m in client.calls[0]["messages"] if m.get("role") == "user")
    assert "Previous attempts" not in prompt


def test_past_the_payload_cap_only_the_pitfalls_are_replayed():
    """A payload anchors. Keep handing the same wrong draft back and the model
    repairs its own bad idea instead of reconsidering it; the reasons still go."""
    plan, goal = _enrichment_plan_and_goal()
    fp = _fingerprint_for(plan, goal)
    client = _accepting_client()
    reasoner = OpenAIReasoner(
        client,
        CAPS,
        repository_reader=FakeRepositoryReader(),
        prior_attempts=FakePriorAttempts(
            [
                _artifact(
                    fp,
                    sequence=n,
                    payload={"marker": "WRONG_DRAFT"},
                    reasons=(f"reason {n}",),
                )
                for n in (3, 2, 1)
            ]
        ),
    )

    asyncio.run(reasoner.enrich_goal_contract(plan, goal, CAPS))

    prompt = " ".join(m["content"] for m in client.calls[0]["messages"] if m.get("role") == "user")
    assert "WRONG_DRAFT" not in prompt
    assert "reason 3" in prompt and "reason 1" in prompt
    assert "deliberately not shown" in prompt


def test_no_prior_attempt_store_leaves_the_prompt_untouched():
    plan, goal = _enrichment_plan_and_goal()
    client = _accepting_client()
    reasoner = OpenAIReasoner(client, CAPS, repository_reader=FakeRepositoryReader())

    asyncio.run(reasoner.enrich_goal_contract(plan, goal, CAPS))

    prompt = " ".join(m["content"] for m in client.calls[0]["messages"] if m.get("role") == "user")
    assert "Previous attempts" not in prompt


def test_the_turn_budget_grows_with_each_replayed_attempt():
    """A retry that starts from a rejected draft plus known pitfalls needs fewer
    READ turns but must still be guaranteed its submission and repair. Giving
    every attempt the same budget means a goal that needed one more turn never
    gets it, however much better informed it now is."""
    import pytest

    from src.app.ports import ReasonerUnavailable

    plan, goal = _enrichment_plan_and_goal()
    fp = _fingerprint_for(plan, goal)
    prior = [_artifact(fp, sequence=n, reasons=(f"reason {n}",)) for n in (2, 1)]

    # never submits: the session dies on its budget, and the message names it
    client = FakeLLMClient([tool_turn("read_project_plan", {}, f"r{n}") for n in range(20)])
    reasoner = OpenAIReasoner(
        client,
        CAPS,
        enrich_max_turns=4,
        repository_reader=FakeRepositoryReader(),
        prior_attempts=FakePriorAttempts(prior),
    )

    with pytest.raises(ReasonerUnavailable) as excinfo:
        asyncio.run(reasoner.enrich_goal_contract(plan, goal, CAPS))

    # base 4 + 2 prior attempts x 2 = 8
    assert "exceeded 8 turns" in str(excinfo.value)


def test_the_escalating_budget_is_capped():
    """Unbounded growth would let one goal spend a whole provider budget."""
    from src.infra.reasoner.openai_reasoner import MAX_ENRICH_TURN_ESCALATION

    assert MAX_ENRICH_TURN_ESCALATION <= 8


def test_a_replan_never_replays_the_superseded_cycles_attempts():
    """A replan mints brand-new goal ids (`activate_cycle` calls `new_id()` per
    outline), so the artifacts of the superseded cycle's goals are addressed by
    ids nothing asks for again. Belt and braces: even if an id DID repeat, the
    fingerprint carries the active cycle id, so the replay is discarded anyway.
    """
    plan, goal = _enrichment_plan_and_goal()
    stale = _artifact(
        _fingerprint_for(plan, goal),
        payload={"marker": "FROM_THE_SUPERSEDED_CYCLE"},
        reasons=("a pitfall from the old cycle",),
    )

    # the replan: same plan, a NEW active cycle carrying a NEW goal
    replanned_goal = Goal(
        id="g2-new", name="Implement greet", position=0, description="return a greeting"
    )
    plan.cycles = [
        Cycle(
            id="cycle-2",
            intent_proposal_id="intent-2",
            draft_id="draft-2",
            status=CycleStatus.ACTIVE,
            goals=[replanned_goal],
            started_at=T0,
        )
    ]
    client = _accepting_client()
    reasoner = OpenAIReasoner(
        client,
        CAPS,
        repository_reader=FakeRepositoryReader(),
        prior_attempts=FakePriorAttempts([stale]),
    )

    asyncio.run(reasoner.enrich_goal_contract(plan, replanned_goal, CAPS))

    prompt = " ".join(m["content"] for m in client.calls[0]["messages"] if m.get("role") == "user")
    assert "FROM_THE_SUPERSEDED_CYCLE" not in prompt
    assert "a pitfall from the old cycle" not in prompt


def test_the_same_goal_in_a_new_cycle_gets_a_different_fingerprint():
    """The cycle id is in the fingerprint precisely so a goal that looks
    identical after a replan cannot inherit the old cycle's rejections."""
    from src.infra.reasoner.openai_reasoner import _enrichment_fingerprint

    plan, goal = _enrichment_plan_and_goal()
    before = _enrichment_fingerprint(plan, goal, {c.id for c in CAPS})

    plan.cycles[0].id = "cycle-superseding"
    after = _enrichment_fingerprint(plan, goal, {c.id for c in CAPS})

    assert before != after


def test_editing_the_goal_invalidates_its_replay():
    """`update_goal` changes name/description, which is what the contract was
    written against. A replay from before the edit describes work no longer
    asked for."""
    from src.infra.reasoner.openai_reasoner import _enrichment_fingerprint

    plan, goal = _enrichment_plan_and_goal()
    before = _enrichment_fingerprint(plan, goal, {c.id for c in CAPS})

    goal.description = "return a greeting, and log it"
    assert _enrichment_fingerprint(plan, goal, {c.id for c in CAPS}) != before

    goal.description = "return a greeting"
    goal.depends_on = ["g0"]
    assert _enrichment_fingerprint(plan, goal, {c.id for c in CAPS}) != before


def test_a_changed_capability_catalog_invalidates_the_replay():
    """A contract's required_capabilities are drawn from the catalog; if the
    catalog moved, the rejected draft may be wrong for a new reason."""
    from src.infra.reasoner.openai_reasoner import _enrichment_fingerprint

    plan, goal = _enrichment_plan_and_goal()

    assert _enrichment_fingerprint(plan, goal, {"backend"}) != _enrichment_fingerprint(
        plan, goal, {"backend", "frontend"}
    )
