"""THE FULL CYCLE driven by the REAL reasoner implementation (OpenAIReasoner)
on a scripted FakeLLMClient — the same 9-phase + replan walk as
test_full_cycle.py (which stays on the stub, the deterministic dry-run gate),
but exercising the production planning path: tool-calling sessions, the
question turn, submit_goals commits, the per-goal submit_tasks JIT step and
the plain-text history replay."""

from __future__ import annotations

import asyncio

import pytest

from src.app.handlers.planning_handler import PlanningHandler
from src.app.testing.fakes import (
    CollectingEventSink,
    DummyAgentRunner,
    FakeClock,
    InMemoryAgentRepository,
    InMemoryCapabilityRepository,
    InMemoryChatStore,
    NoOpWorkspace,
)
from src.app.use_cases.control import finish_review, resume_from_review, review_replan
from src.app.use_cases.conversation import discovery_message, replanning_message
from src.app.use_cases.create_plan import create_plan
from src.app.use_cases.run_worker import worker_tick
from src.domain.aggregates.planner_orchestrator import PlanPhase
from src.domain.entities.capability import Capability
from src.domain.entities.project_definition import ProjectDefinition
from src.domain.value_objects.lifecycle import Status
from src.infra.db.engine import build_engine, make_session_factory
from src.infra.db.tables import Base
from src.infra.db.reference_repos import SqliteProjectRepository
from src.infra.db.unit_of_work import SqliteUnitOfWork
from src.infra.reasoner.openai_reasoner import OpenAIReasoner
from tests.fakes_llm import FakeLLMClient, text_turn, tool_turn
from tests.support import make_agent_spec

pytestmark = pytest.mark.integration

CAPS = [Capability(id="backend", name="Backend", description="server-side")]

# The LLM script, in pop order across the whole cycle:
#   1. discovery turn 1  -> plain text (the question turn, no commit)
#   2. discovery turn 2  -> submit_goals: API (1 task, caps) + Docs (task-less)
#   3. ENRICHING JIT     -> submit_tasks for Docs (2 ordered tasks)
#   4. replanning turn   -> submit_goals: Hardening (1 task)
SCRIPT = [
    text_turn("What kind of docs do you need?"),
    tool_turn(
        "submit_goals",
        {
            "goals": [
                {
                    "name": "API",
                    "description": "build the api",
                    "tasks": [
                        {
                            "name": "scaffold app",
                            "description": "fastapi skeleton",
                            "required_capabilities": ["backend"],
                        }
                    ],
                },
                {"name": "Docs", "description": "user documentation"},
            ]
        },
        "c-goals",
    ),
    tool_turn(
        "submit_tasks",
        {
            "tasks": [
                {"name": "write quickstart", "description": "README quickstart"},
                {"name": "write api reference", "description": "endpoint docs"},
            ]
        },
        "c-tasks",
    ),
    tool_turn(
        "submit_goals",
        {
            "goals": [
                {
                    "name": "Hardening",
                    "description": "make it production-ready",
                    "tasks": [{"name": "add auth", "description": "token auth"}],
                }
            ]
        },
        "c-replan",
    ),
]


class LLMStack:
    def __init__(self, tmp_path):
        engine = build_engine(f"sqlite:///{tmp_path / 'llm.db'}")
        Base.metadata.create_all(engine)
        sf = make_session_factory(engine)
        SqliteProjectRepository(sf).add(
            ProjectDefinition(id="project-1", name="Test project", repo_url=None)
        )
        self.clock = FakeClock()
        self.uow = SqliteUnitOfWork(sf, self.clock)
        self.llm = FakeLLMClient(list(SCRIPT))
        self.reasoner = OpenAIReasoner(self.llm, CAPS)
        self.runner = DummyAgentRunner({})
        self.agents = InMemoryAgentRepository([make_agent_spec()], "a1")
        self.capabilities = InMemoryCapabilityRepository(CAPS)
        self.chat = InMemoryChatStore()
        self.ws = NoOpWorkspace()
        self.sink = CollectingEventSink()
        self.planning = PlanningHandler(self.reasoner, self.agents, self.capabilities, self.clock)

    def tick(self):
        return asyncio.run(
            worker_tick(
                self.uow,
                self.runner,
                self.agents,
                self.ws,
                self.sink,
                self.clock,
                "w1",
                60,
                planning_handler=self.planning,
            )
        )

    def drain(self, max_ticks=30):
        for _ in range(max_ticks):
            if not self.tick():
                return
        raise AssertionError("worker did not converge")

    def say(self, plan_id, message, *, replanning=False):
        fn = replanning_message if replanning else discovery_message
        return asyncio.run(fn(plan_id, message, self.uow, self.reasoner, self.chat, self.clock))

    def plan(self, plan_id):
        with self.uow:
            return self.uow.plans.get(plan_id)


def test_full_cycle_on_the_real_reasoner_with_scripted_llm(tmp_path):
    stack = LLMStack(tmp_path)
    plan_id = create_plan("Build a tiny service with docs", "project-1", "req-1", stack.uow)

    # ---- DISCOVERY: question turn, then the commit turn ----
    asked = stack.say(plan_id, "I want a tiny service")
    assert asked.committed is False
    assert asked.reply == "What kind of docs do you need?"
    assert stack.plan(plan_id).phase == PlanPhase.DISCOVERY

    committed = stack.say(plan_id, "quickstart plus api reference")
    assert committed.committed is True
    plan = stack.plan(plan_id)
    assert plan.phase == PlanPhase.ARCHITECTURE
    # the second converse call replayed turn 1 as plain text history
    second_call = stack.llm.calls[1]["messages"]
    assert {"role": "user", "content": "I want a tiny service"} in second_call
    assert {
        "role": "assistant",
        "content": "What kind of docs do you need?",
    } in second_call

    # ---- worker: ARCHITECTURE passthrough + ENRICHING JIT + binding ----
    stack.drain()
    plan = stack.plan(plan_id)
    assert plan.phase == PlanPhase.AWAITING_REVIEW
    api_goal, docs_goal = plan.goals
    assert [t.name for t in api_goal.tasks] == ["scaffold app"]  # pre-populated
    assert api_goal.tasks[0].required_capabilities == ["backend"]
    assert [(t.name, t.position) for t in docs_goal.tasks] == [
        ("write quickstart", 0),
        ("write api reference", 1),
    ]
    assert all(t.agent_id == "a1" for g in plan.goals for t in g.tasks)

    # ---- the gates + execution ----
    resume_from_review(plan_id, stack.uow)
    stack.drain()
    plan = stack.plan(plan_id)
    assert plan.phase == PlanPhase.REVIEW
    assert all(t.status == Status.DONE for g in plan.goals for t in g.tasks)

    # ---- REPLANNING on real replanning prompt + context ----
    review_replan(plan_id, stack.uow)
    replan = stack.say(plan_id, "harden it for production", replanning=True)
    assert replan.committed is True
    plan = stack.plan(plan_id)
    assert plan.iteration == 2
    assert [g.name for g in plan.goals] == ["API", "Docs", "Hardening"]
    # the replanning prompt carried prior results (include_results=True)
    replan_prompt = stack.llm.calls[3]["messages"][1]["content"]
    assert "Re-planning conversation" in replan_prompt
    assert "history — do not redo" in replan_prompt

    # ---- iteration 2 to DONE ----
    stack.drain()
    resume_from_review(plan_id, stack.uow)
    stack.drain()
    finish_review(plan_id, stack.uow)
    assert stack.plan(plan_id).phase == PlanPhase.DONE

    # chat history holds the whole conversation in order
    rows = stack.chat.list(plan_id)
    assert [(m.role, m.meta.get("committed")) for m in rows] == [
        ("user", None),
        ("assistant", False),
        ("user", None),
        ("assistant", True),
        ("user", None),
        ("assistant", True),
    ]
    assert stack.llm.script == []  # every scripted turn was consumed
