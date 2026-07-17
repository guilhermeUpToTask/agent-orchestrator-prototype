"""Canonical planning artifacts driven by the real OpenAIReasoner implementation
on a scripted client and persisted through the real SQLite UnitOfWork."""

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
from src.app.use_cases.conversation import discovery_message, replanning_message
from src.app.use_cases.create_plan import create_plan
from src.app.use_cases.cyclic_planning import activate_cycle, approve_intent
from src.app.use_cases.run_worker import worker_tick
from src.domain.aggregates.planner_orchestrator import PlanPhase
from src.domain.entities.planning_artifacts import PlanStatus
from src.domain.entities.capability import Capability
from src.domain.entities.project_definition import ProjectDefinition
from src.infra.db.engine import build_engine, make_session_factory
from src.infra.db.tables import Base
from src.infra.db.reference_repos import SqliteProjectRepository
from src.infra.db.unit_of_work import SqliteUnitOfWork
from src.infra.reasoner.openai_reasoner import OpenAIReasoner
from tests.fakes_llm import FakeLLMClient, text_turn, tool_turn
from tests.support import make_agent_spec

pytestmark = pytest.mark.integration

CAPS = [Capability(id="backend", name="Backend", description="server-side")]

# The LLM script follows the canonical purpose profiles in order.
SCRIPT = [
    text_turn("What kind of docs do you need?"),
    tool_turn(
        "submit_intent_proposal",
        {
            "normalized_brief": "Build a tiny documented API service.",
            "objective": "Ship a maintainable API with documentation.",
            "scope": ["HTTP API", "documentation"],
            "constraints": ["SQLite"],
            "exclusions": ["mobile client"],
            "assumptions": ["single tenant"],
            "unresolved_questions": [],
        },
        "c-intent",
    ),
    tool_turn(
        "submit_cycle_draft",
        {
            "goals": [
                {
                    "key": "delivery",
                    "name": "API delivery",
                    "objective": "Build and document the API.",
                    "position": 0,
                    "depends_on": [],
                }
            ]
        },
        "c-draft",
    ),
    tool_turn(
        "submit_goal_contract",
        {
            "objective": "Build and document the API.",
            "acceptance_criteria": [{"id": "g-1", "description": "API is usable"}],
            "tasks": [
                {
                    "objective": "Implement the service and quickstart.",
                    "acceptance_criteria": [{"id": "t-1", "description": "health endpoint works"}],
                    "goal_criterion_ids": ["g-1"],
                    "allowed_scope": ["backend/", "README.md"],
                    "forbidden_scope": ["frontend/"],
                    "verification_commands": ["pytest -q"],
                    "verification_strategy": "executable_check",
                    "required_capabilities": ["backend"],
                }
            ],
            "cross_task_integration_criterion_ids": [],
            "required_capabilities": ["backend"],
        },
        "c-contract",
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
        implementation = Capability(
            id="implementation", name="Implementation", description="implements changes"
        )
        test_authoring = Capability(
            id="test_authoring", name="Test authoring", description="authors tests"
        )
        agent = make_agent_spec().model_copy(
            update={"capabilities": [CAPS[0], implementation, test_authoring]}
        )
        self.agents = InMemoryAgentRepository([agent], "a1")
        self.capabilities = InMemoryCapabilityRepository([*CAPS, implementation, test_authoring])
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


def test_canonical_planning_on_the_real_reasoner_with_scripted_llm(tmp_path):
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
    assert plan.status == PlanStatus.WAITING
    assert plan.intent_proposal is not None
    assert plan.intent_proposal.objective == "Ship a maintainable API with documentation."
    # the second converse call replayed turn 1 as plain text history
    second_call = stack.llm.calls[1]["messages"]
    assert {"role": "user", "content": "I want a tiny service"} in second_call
    assert {
        "role": "assistant",
        "content": "What kind of docs do you need?",
    } in second_call

    assert plan.review_gate is not None
    approve_intent(plan_id, plan.review_gate.id, 1, stack.uow, stack.clock)
    asyncio.run(stack.planning.handle(plan_id, stack.plan(plan_id), stack.uow))
    plan = stack.plan(plan_id)
    assert plan.cycle_draft is not None and plan.review_gate is not None
    assert [outline.key for outline in plan.cycle_draft.goals] == ["delivery"]
    activate_cycle(plan_id, plan.review_gate.id, 1, stack.uow, stack.clock)
    asyncio.run(stack.planning.handle(plan_id, stack.plan(plan_id), stack.uow))
    plan = stack.plan(plan_id)
    assert plan.active_cycle is not None
    (goal,) = plan.active_cycle.goals
    assert goal.contract is not None
    assert goal.tasks[0].contract is not None
    assert goal.tasks[0].required_capabilities == ["backend"]
    assert goal.tasks[0].role_agent_ids["implementer"] == "a1"

    # chat history holds the whole conversation in order
    rows = stack.chat.list(plan_id)
    assert [(m.role, m.meta.get("committed")) for m in rows] == [
        ("user", None),
        ("assistant", False),
        ("user", None),
        ("assistant", True),
    ]
    assert stack.llm.script == []  # every scripted turn was consumed
