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
from src.domain.ports.reasoner_port import ChatMessage
from src.infra.reasoner.openai_reasoner import OpenAIReasoner
from tests.fakes_llm import FakeLLMClient, text_turn, tool_turn

T0 = datetime(2026, 7, 3, tzinfo=timezone.utc)
CAPS = [Capability(id="backend", name="Backend", description="")]


def make_plan(phase=PlanPhase.DISCOVERY):
    return Plan(id="p1", brief="tiny service", phase=phase)


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


def test_submit_goals_commits_roadmap_with_ids_and_positions():
    client = FakeLLMClient(
        [
            tool_turn(
                "submit_goals",
                {
                    "goals": [
                        {
                            "name": "API",
                            "description": "build it",
                            "tasks": [
                                {
                                    "name": "scaffold",
                                    "description": "make the app",
                                    "required_capabilities": ["backend"],
                                }
                            ],
                        },
                        {"name": "Docs", "description": "write them"},
                    ]
                },
            )
        ]
    )
    reply = converse(OpenAIReasoner(client, CAPS), make_plan(), [], "go")

    assert reply.goals is not None
    g1, g2 = reply.goals
    assert (g1.name, g1.position) == ("API", 0)
    assert (g2.name, g2.position) == ("Docs", 1)
    assert g1.id and g2.id and g1.id != g2.id
    (t1,) = g1.tasks
    assert (t1.name, t1.position, t1.required_capabilities) == (
        "scaffold", 0, ["backend"],
    )
    assert g2.tasks == []  # task-less goal flows to the ENRICHING JIT
    assert "2 goal(s)" in reply.message


def test_invalid_submit_feeds_errors_back_then_commits():
    client = FakeLLMClient(
        [
            tool_turn("submit_goals", {"goals": []}, "c1"),
            tool_turn(
                "submit_goals",
                {"goals": [{"name": "API", "description": "d"}]},
                "c2",
            ),
        ]
    )
    reply = converse(OpenAIReasoner(client, CAPS), make_plan(), [], "go")

    assert reply.goals is not None and len(reply.goals) == 1
    # the rejection reached the model as a tool message
    second_call = client.calls[1]["messages"]
    rejections = [
        m for m in second_call if m.get("role") == "tool" and "non-empty" in m["content"]
    ]
    assert rejections


def test_enrich_goal_builds_ordered_tasks():
    client = FakeLLMClient(
        [
            tool_turn(
                "submit_tasks",
                {
                    "tasks": [
                        {"name": "write models", "description": "d1",
                         "required_capabilities": ["backend"]},
                        {"name": "wire routes", "description": "d2"},
                    ]
                },
            )
        ]
    )
    goal = Goal(id="g1", name="API", position=0, description="", tasks=[])
    tasks = asyncio.run(
        OpenAIReasoner(client, CAPS).enrich_goal(make_plan(), goal, CAPS)
    )

    assert [(t.name, t.position) for t in tasks] == [
        ("write models", 0), ("wire routes", 1),
    ]
    assert tasks[0].required_capabilities == ["backend"]
    assert all(t.id for t in tasks)
    # the prompt carried the capability catalog
    prompt = client.calls[0]["messages"][1]["content"]
    assert "`backend`" in prompt and "API" in prompt


def test_unknown_capability_rejected_then_filtered_after_budget():
    bad_submit = {
        "tasks": [
            {"name": "t", "description": "d",
             "required_capabilities": ["backend", "made-up"]}
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
    tasks = asyncio.run(
        OpenAIReasoner(client, CAPS).enrich_goal(make_plan(), goal, CAPS)
    )

    # two rejections with the unknown id named, then the filtered accept
    assert len(client.calls) == 3
    first_rejection = json.loads(
        next(
            m["content"]
            for m in client.calls[1]["messages"]
            if m.get("role") == "tool"
        )
    )
    assert first_rejection["accepted"] is False
    assert "made-up" in first_rejection["errors"][0]
    (task,) = tasks
    assert task.required_capabilities == ["backend"]  # unknown id filtered


# ---- token/usage telemetry (un-freeze #3, WP5) ----
def test_converse_emits_llm_call_with_summed_usage():
    from src.app.testing.fakes import CollectingEventSink

    client = FakeLLMClient(
        [
            # first submit is rejected (empty goals) -> session continues
            tool_turn(
                "submit_goals",
                {"goals": []},
                "c1",
                usage={"prompt_tokens": 10, "completion_tokens": 5,
                       "total_tokens": 15},
            ),
            tool_turn(
                "submit_goals",
                {"goals": [{"name": "G", "description": "d"}]},
                "c2",
                usage={"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
            ),
        ]
    )
    sink = CollectingEventSink()
    reasoner = OpenAIReasoner(client, CAPS, event_sink=sink)

    # a rejected submit then an accepted one: 2 llm calls, usage summed
    reply = converse(reasoner, make_plan(), [], "go")
    assert reply.goals is not None

    (event,) = sink.events
    assert event.type == "llm.call"
    assert event.task_id is None  # plan-scoped telemetry
    assert event.plan_id == "p1"
    assert event.payload["mode"] == "discovery"
    assert event.payload["llm_calls"] == "2"
    assert event.payload["prompt_tokens"] == "30"
    assert event.payload["completion_tokens"] == "13"
    assert event.payload["total_tokens"] == "43"


def test_enrich_emits_llm_call_and_missing_usage_is_zero():
    from src.app.testing.fakes import CollectingEventSink

    # no usage scripted -> counters default to 0, still emits
    client = FakeLLMClient(
        [tool_turn("submit_tasks", {"tasks": [{"name": "t", "description": "d"}]})]
    )
    sink = CollectingEventSink()
    goal = Goal(id="g1", name="API", position=0, description="", tasks=[])
    asyncio.run(
        OpenAIReasoner(client, CAPS, event_sink=sink).enrich_goal(
            make_plan(PlanPhase.ENRICHING), goal, CAPS
        )
    )

    (event,) = sink.events
    assert event.type == "llm.call" and event.payload["mode"] == "enrich"
    assert event.payload["total_tokens"] == "0"


def test_no_event_sink_is_a_silent_noop():
    client = FakeLLMClient([text_turn("hi")])
    # no event_sink: converse still works, nothing to emit
    reply = converse(OpenAIReasoner(client, CAPS), make_plan(), [], "go")
    assert reply.message == "hi"
