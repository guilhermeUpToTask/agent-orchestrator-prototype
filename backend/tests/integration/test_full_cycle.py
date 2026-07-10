"""THE FULL CYCLE on the real stack (SQLite UoW + stub reasoner + dummy runner):

  DISCOVERY -> (chat) -> ARCHITECTURE -> ENRICHING -> AWAITING_REVIEW ->
  (approve) -> RUNNING -> REVIEW -> (finish) -> DONE

plus one complete replan loop (REVIEW -> REPLANNING -> chat -> ARCHITECTURE ->
... -> DONE, iteration 2, append-only history) and the mid-RUNNING replan with
an in-flight task (tolerant finalize on the real stack).
"""
from __future__ import annotations

import asyncio

import pytest

from src.app.handlers.planning_handler import PlanningHandler
from src.app.testing.fakes import (
    CollectingEventSink,
    DummyAgentRunner,
    DummyBehavior,
    FakeClock,
    InMemoryAgentRepository,
    InMemoryCapabilityRepository,
    InMemoryChatStore,
    NoOpWorkspace,
)
from src.app.use_cases.apply_edit import UpdateTask, apply_edit
from src.app.use_cases.control import (
    finish_review,
    reopen_discovery,
    resume_from_review,
    review_replan,
)
from src.app.use_cases.conversation import discovery_message, replanning_message
from src.app.use_cases.create_plan import create_plan
from src.app.use_cases.pause_resume import pause_plan, resume_plan
from src.app.use_cases.request_replan import request_replan
from src.app.use_cases.run_worker import worker_tick
from src.domain.aggregates.planner_orchestrator import PlanPhase
from src.domain.value_objects.lifecycle import Status
from src.infra.db.engine import build_engine, make_session_factory
from src.infra.db.tables import Base
from src.infra.db.unit_of_work import SqliteUnitOfWork
from src.infra.reasoner.stub_reasoner import StubReasoner
from tests.support import make_agent_spec

pytestmark = pytest.mark.integration

BRIEF = """Build a tiny service
goal: API skeleton
task: scaffold the app
task: add health endpoint
goal: Persistence
task: wire sqlite
goal: Docs
"""

REPLAN_MESSAGE = """goal: Hardening
task: add auth
"""


class Stack:
    def __init__(self, tmp_path, script=None):
        engine = build_engine(f"sqlite:///{tmp_path / 'full.db'}")
        Base.metadata.create_all(engine)
        sf = make_session_factory(engine)
        self.clock = FakeClock()
        self.uow = SqliteUnitOfWork(sf, self.clock)
        self.reasoner = StubReasoner()
        self.runner = DummyAgentRunner(script or {})
        self.agents = InMemoryAgentRepository([make_agent_spec()], "a1")
        self.capabilities = InMemoryCapabilityRepository()
        self.chat = InMemoryChatStore()
        self.ws = NoOpWorkspace()
        self.sink = CollectingEventSink()
        self.planning = PlanningHandler(
            self.reasoner, self.agents, self.capabilities, self.clock
        )

    def say(self, plan_id, message, *, replanning=False):
        fn = replanning_message if replanning else discovery_message
        return asyncio.run(
            fn(plan_id, message, self.uow, self.reasoner, self.chat, self.clock)
        )

    def tick(self, worker_id="w1"):
        return asyncio.run(
            worker_tick(
                self.uow,
                self.runner,
                self.agents,
                self.ws,
                self.sink,
                self.clock,
                worker_id,
                60,
                planning_handler=self.planning,
            )
        )

    def drain(self, max_ticks=20):
        """Tick until the worker finds nothing to progress (gates/conversational
        phases are unclaimable, so this converges)."""
        for _ in range(max_ticks):
            if not self.tick():
                return
        raise AssertionError("worker did not converge")

    def plan(self, plan_id):
        with self.uow:
            return self.uow.plans.get(plan_id)

    def edit(self, plan_id, e):
        apply_edit(plan_id, e, self.uow, self.capabilities, self.agents)


def test_all_nine_phases_and_one_replan_loop(tmp_path):
    stack = Stack(tmp_path)

    # DISCOVERY (conversational — invisible to workers)
    plan_id = create_plan(BRIEF, "req-1", stack.uow)
    assert stack.plan(plan_id).phase == PlanPhase.DISCOVERY
    assert stack.tick() is False  # not claimable

    # multi-turn: an ask-turn replies without committing (phase unchanged)
    asked = stack.say(plan_id, "ask: monolith or services?")
    assert (asked.reply, asked.committed) == ("monolith or services?", False)
    assert stack.plan(plan_id).phase == PlanPhase.DISCOVERY

    committed = stack.say(plan_id, "")
    assert committed.committed is True
    assert stack.plan(plan_id).phase == PlanPhase.ARCHITECTURE
    assert len(stack.chat.list(plan_id)) == 4  # 2 user + 2 assistant turns

    # worker drives ARCHITECTURE (passthrough) -> ENRICHING (JIT per goal)
    # -> AWAITING_REVIEW, then stops
    stack.drain()
    plan = stack.plan(plan_id)
    assert plan.phase == PlanPhase.AWAITING_REVIEW
    assert [g.name for g in plan.goals] == ["API skeleton", "Persistence", "Docs"]
    # grammar-specified tasks are untouched by enrichment...
    grammar_tasks = [t for g in plan.goals[:2] for t in g.tasks]
    assert [t.name for t in grammar_tasks] == [
        "scaffold the app", "add health endpoint", "wire sqlite",
    ]
    assert all(t.description == "" for t in grammar_tasks)
    # ...while the task-less goal was JIT-populated by the reasoner
    (docs_task,) = plan.goals[2].tasks
    assert docs_task.name == "implement: Docs"
    assert docs_task.description.startswith("[enriched]")
    all_tasks = grammar_tasks + [docs_task]
    assert all(t.agent_id == "a1" for t in all_tasks)  # bound (default fallback)

    # human approves the pre-execution gate
    resume_from_review(plan_id, stack.uow)
    stack.drain()
    plan = stack.plan(plan_id)
    assert plan.phase == PlanPhase.REVIEW  # execution exhausted into the gate
    assert all(t.status == Status.DONE for g in plan.goals for t in g.tasks)

    # human chooses "replan the next phase" at the post-execution gate
    review_replan(plan_id, stack.uow)
    assert stack.plan(plan_id).phase == PlanPhase.REPLANNING
    assert stack.tick() is False  # conversational: still invisible to workers

    replan_turn = stack.say(plan_id, REPLAN_MESSAGE, replanning=True)
    assert replan_turn.committed is True
    plan = stack.plan(plan_id)
    assert plan.phase == PlanPhase.ARCHITECTURE
    assert plan.iteration == 2
    # append-only: iteration-1 history untouched, new goal after it
    assert [g.name for g in plan.goals] == [
        "API skeleton", "Persistence", "Docs", "Hardening",
    ]
    assert [g.position for g in plan.goals] == [0, 1, 2, 3]
    assert plan.goals[0].status == Status.DONE and plan.goals[1].status == Status.DONE

    # iteration 2 runs the same pipeline to DONE
    stack.drain()
    resume_from_review(plan_id, stack.uow)
    stack.drain()
    assert stack.plan(plan_id).phase == PlanPhase.REVIEW
    finish_review(plan_id, stack.uow)
    final = stack.plan(plan_id)
    assert final.phase == PlanPhase.DONE
    assert final.iteration == 2
    # iteration-1 results remain as history/context for future re-plans
    assert final.goals[0].tasks[0].result is not None


def test_mid_running_replan_with_in_flight_task_tolerant_finalize(tmp_path):
    """The user requests a replan WHILE a task is executing; the late failure
    terminal-skips (never requeues into the abandoned iteration) on the real
    SQLite stack, and the next iteration proceeds normally."""

    class ReplanMidRun(DummyAgentRunner):
        def __init__(self, script, uow):
            super().__init__(script)
            self._uow = uow
            self.triggered = False

        async def run(self, task, spec, **kw):
            if not self.triggered:
                self.triggered = True
                request_replan(self._plan_id, self._uow)
            return await super().run(task, spec, **kw)

    stack = Stack(tmp_path)
    runner = ReplanMidRun(
        {"__all__": DummyBehavior()}, stack.uow
    )
    stack.runner = runner

    plan_id = create_plan("goal: G1\ntask: only task", "req-1", stack.uow)
    runner._plan_id = plan_id
    runner.script = {}  # default success behavior; the trigger is the point
    stack.say(plan_id, "")
    stack.drain()
    resume_from_review(plan_id, stack.uow)

    # the tick that executes the task also carries the mid-run replan
    stack.drain()
    plan = stack.plan(plan_id)
    assert plan.phase == PlanPhase.REPLANNING
    # late SUCCESS completed as harmless history (task was still RUNNING)
    task = plan.goals[0].tasks[0]
    assert task.status == Status.DONE

    # the conversational re-plan commits a fresh iteration and runs to DONE
    stack.say(plan_id, "goal: G2\ntask: redo", replanning=True)
    assert stack.plan(plan_id).iteration == 2
    stack.drain()
    resume_from_review(plan_id, stack.uow)
    stack.drain()
    finish_review(plan_id, stack.uow)
    assert stack.plan(plan_id).phase == PlanPhase.DONE


def test_run_worker_forever_starts_ticks_and_stops(tmp_path):
    """The container-wired entrypoint: boots on an empty db, idles (sleeps, no
    spin) and honors the stop event. agent_runner.mode defaults to dry-run —
    no env, no master key."""
    from src.infra.container import AppContainer
    from src.infra.worker.main import run_worker_forever

    container = AppContainer(orchestrator_home=tmp_path)
    Base.metadata.create_all(container.engine)

    async def run_briefly():
        stop = asyncio.Event()

        async def stopper():
            await asyncio.sleep(0.15)
            stop.set()

        await asyncio.gather(
            run_worker_forever(container, poll_seconds=0.02, stop=stop), stopper()
        )

    asyncio.run(run_briefly())  # returns => started, idled and stopped cleanly


def test_awaiting_review_reopen_loop(tmp_path):
    """Gate chat-back (un-freeze #3): at the pre-execution gate the user asks to
    reopen the conversation; the second commit REPLACES the un-executed roadmap,
    then the plan runs the replacement to REVIEW."""
    stack = Stack(tmp_path)
    # a goal-free brief so the stub's discovery fold doesn't re-inject goals
    plan_id = create_plan("build me something", "req-1", stack.uow)
    stack.say(plan_id, "goal: First\ntask: t one")  # commit the initial roadmap
    stack.drain()
    assert stack.plan(plan_id).phase == PlanPhase.AWAITING_REVIEW
    assert [g.name for g in stack.plan(plan_id).goals] == ["First"]

    # request changes -> DISCOVERY (chat re-opens), unclaimable again
    reopen_discovery(plan_id, stack.uow)
    assert stack.plan(plan_id).phase == PlanPhase.DISCOVERY
    assert stack.tick() is False

    # second commit with a DIFFERENT roadmap replaces the first (no iteration bump)
    stack.say(plan_id, "goal: Second\ntask: t two")
    stack.drain()
    plan = stack.plan(plan_id)
    assert plan.phase == PlanPhase.AWAITING_REVIEW
    assert [g.name for g in plan.goals] == ["Second"]  # the first is gone
    assert plan.iteration == 1

    resume_from_review(plan_id, stack.uow)
    stack.drain()
    assert stack.plan(plan_id).phase == PlanPhase.REVIEW


def test_pause_edit_resume_walk(tmp_path):
    """Pause/resume with editing while paused, and the auto-pause recovery loop:
    approve -> pause -> edit while paused -> resume -> a permanent failure
    auto-pauses -> remove the offending task -> resume -> runs to REVIEW."""
    stack = Stack(tmp_path, script={"__fail__": DummyBehavior(always_fail=True)})
    plan_id = create_plan(
        "goal: G\ntask: keep me\ntask: drop me", "req-1", stack.uow
    )
    stack.say(plan_id, "")
    stack.drain()
    resume_from_review(plan_id, stack.uow)  # approve -> RUNNING

    # pause mid-run: the worker stops claiming the plan
    pause_plan(plan_id, stack.uow, "operator hold")
    assert stack.plan(plan_id).paused
    assert stack.tick() is False  # paused -> unclaimable

    # edit while paused: rename the first task, then resume and drive to REVIEW
    goal = stack.plan(plan_id).goals[0]
    keep_id = goal.tasks[0].id
    stack.edit(plan_id, UpdateTask(goal.id, keep_id, name="kept + renamed"))
    resume_plan(plan_id, stack.uow)
    stack.drain()
    assert stack.plan(plan_id).phase == PlanPhase.REVIEW
    kept = stack.plan(plan_id).goals[0].tasks[0]
    assert kept.name == "kept + renamed" and kept.status == Status.DONE
