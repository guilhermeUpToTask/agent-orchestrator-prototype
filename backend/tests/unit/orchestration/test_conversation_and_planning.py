"""The conversational phases (multi-turn chat with commit) and the worker-driven
planning phases (ARCHITECTURE passthrough + the ENRICHING JIT step), on both
backends via env_factory.

The reasoner here is scripted per test (not the stub): conversation tests
control exactly when goals are committed; enrich tests control the returned
task sets and can misbehave on purpose (crash, race) to prove the guards.
"""

from __future__ import annotations

import asyncio

import pytest

from src.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from src.domain.entities.goal import Goal
from src.domain.entities.planning_artifacts import Cycle, CycleStatus, PlanBlock, PlanStatus
from src.domain.entities.task import Task
from src.domain.errors.planning_errors import InvalidEditError
from src.domain.ports.reasoner_port import ChatMessage, IntentCandidate, ReasonerReply
from src.domain.value_objects.lifecycle import Status
from src.domain.value_objects.tasks_vos import TaskResult

from src.app.handlers.base import Signal
from src.app.handlers.planning_handler import PlanningHandler
from src.app.testing.fakes import (
    InMemoryCapabilityRepository,
    InMemoryChatStore,
)
from src.app.use_cases.conversation import discovery_message, replanning_message
from src.app.use_cases.request_replan import request_replan


def goal(gid: str, position: int, tasks: list[Task] | None = None) -> Goal:
    return Goal(id=gid, name=gid, position=position, description="", tasks=tasks or [])


def task(tid: str, position: int = 0) -> Task:
    return Task(id=tid, name=tid, position=position, description="")


def plan_in(phase: PlanPhase, goals: list[Goal] | None = None) -> Plan:
    return Plan(project_id="project-1", id="p1", brief="the brief", phase=phase, goals=goals or [])


class ScriptedReasoner:
    """converse() pops scripted replies; enrich_goal() returns scripted task
    sets per goal id (and can run a side effect first — the race hook)."""

    def __init__(self, replies=None, task_sets=None, before_enrich=None):
        self.replies = list(replies or [])
        self.task_sets = dict(task_sets or {})
        self.before_enrich = before_enrich
        self.converse_calls: list[tuple[str, str, int]] = []
        self.enriched_goal_ids: list[str] = []

    async def converse(self, plan, history, message, mode):
        self.converse_calls.append((mode, message, len(history)))
        return self.replies.pop(0)

    async def enrich_goal(self, plan, goal, capabilities):
        self.enriched_goal_ids.append(goal.id)
        if self.before_enrich is not None:
            self.before_enrich()
        return [
            t.model_copy(deep=True) for t in self.task_sets.get(goal.id, [task("t-" + goal.id)])
        ]


def handler(reasoner, env, capabilities=None):
    return PlanningHandler(
        reasoner, env.agents, InMemoryCapabilityRepository(capabilities), env.clock
    )


def turn(env, chat, reasoner, message, *, replanning=False):
    fn = replanning_message if replanning else discovery_message
    return asyncio.run(fn("p1", message, env.uow, reasoner, chat, env.clock))


# ---------------------------------------------------------------------------
# conversation — multi-turn with commit
# ---------------------------------------------------------------------------


def test_ask_turn_keeps_phase_and_persists_both_messages(env_factory):
    env = env_factory()
    env.seed(plan_in(PlanPhase.DISCOVERY))
    chat = InMemoryChatStore()
    reasoner = ScriptedReasoner(replies=[ReasonerReply(message="which db?")])

    result = turn(env, chat, reasoner, "build me a service")

    assert (result.reply, result.committed) == ("which db?", False)
    assert result.phase == PlanPhase.DISCOVERY
    assert env.stored("p1").phase == PlanPhase.DISCOVERY  # nothing advanced
    assert env.outbox_types() == []  # no phase event on a non-commit turn
    assert [(m.role, m.content) for m in chat.list("p1")] == [
        ("user", "build me a service"),
        ("assistant", "which db?"),
    ]
    assert chat.list("p1")[1].meta["committed"] is False
    assert chat.list("p1")[1].meta["planning_status"] == "waiting_for_user"
    assert result.operation_status.value == "waiting_for_user"


def test_commit_turn_advances_and_marks_meta(env_factory):
    env = env_factory()
    env.seed(plan_in(PlanPhase.DISCOVERY))
    chat = InMemoryChatStore()
    reasoner = ScriptedReasoner(
        replies=[
            ReasonerReply(message="which db?"),
            ReasonerReply(
                message="intent ready",
                intent=IntentCandidate(
                    normalized_brief="Build a SQLite service.",
                    objective="Ship a service",
                    constraints=["SQLite"],
                    assumptions=["single tenant"],
                ),
            ),
        ]
    )

    turn(env, chat, reasoner, "build me a service")
    result = turn(env, chat, reasoner, "sqlite is fine")

    assert (result.committed, result.phase) == (True, PlanPhase.DISCOVERY)
    stored = env.stored("p1")
    assert stored.status == PlanStatus.WAITING
    assert stored.intent_proposal is not None
    assert stored.intent_proposal.objective == "Ship a service"
    assert stored.review_gate is not None and stored.review_gate.unresolved
    assert stored.goals == []
    assert env.outbox_types() == ["IntentProposed", "ReviewGateOpened"]
    # the second converse call saw the first turn's two messages as history
    assert reasoner.converse_calls[1] == ("discovery", "sqlite is fine", 2)
    assert chat.list("p1")[-1].meta["committed"] is True
    assert chat.list("p1")[-1].meta["normalized_brief"] == "Build a SQLite service."


def test_discovery_after_reopen_proposes_intent_without_mutating_roadmap(env_factory):
    """Reopened chat proposes reviewable intent; roadmap mutation waits for approval."""
    from src.app.use_cases.control import reopen_discovery

    env = env_factory()
    # a plan already enriched and sitting at the pre-execution gate
    old = goal("g-old", 0, [task("t-old")])
    env.seed(plan_in(PlanPhase.AWAITING_REVIEW, [old]))
    chat = InMemoryChatStore()
    chat.append(
        "p1",
        ChatMessage(role="user", content="the original brief", created_at=env.clock.now()),
    )

    reopen_discovery("p1", env.uow)
    assert env.stored("p1").phase == PlanPhase.DISCOVERY

    reasoner = ScriptedReasoner(
        replies=[
            ReasonerReply(
                message="new intent",
                intent=IntentCandidate(
                    normalized_brief="Do it differently.",
                    objective="Change the delivery approach",
                ),
            )
        ]
    )
    result = turn(env, chat, reasoner, "actually, do it differently")

    assert (result.committed, result.phase) == (True, PlanPhase.DISCOVERY)
    stored = env.stored("p1")
    assert [g.id for g in stored.goals] == ["g-old"]
    assert stored.intent_proposal is not None
    assert stored.intent_proposal.objective == "Change the delivery approach"
    # chat history spans the reopen
    assert [m.content for m in chat.list("p1")][0] == "the original brief"


def test_user_message_survives_reasoner_crash(env_factory):
    env = env_factory()
    env.seed(plan_in(PlanPhase.DISCOVERY))
    chat = InMemoryChatStore()

    class ExplodingReasoner(ScriptedReasoner):
        async def converse(self, plan, history, message, mode):
            raise RuntimeError("provider down")

    with pytest.raises(RuntimeError):
        turn(env, chat, ExplodingReasoner(), "precious user words")

    # persisted BEFORE the LLM call: the words survive the crash
    assert [(m.role, m.content) for m in chat.list("p1")] == [("user", "precious user words")]
    assert env.stored("p1").phase == PlanPhase.DISCOVERY
    with env.uow:
        operation = env.uow.executions.list_planning_operations("p1")[-1]
    assert operation.status.value == "failed"
    assert operation.failure_kind == "reasoner_crash"


def test_message_in_wrong_phase_rejected(env_factory):
    env = env_factory()
    env.seed(plan_in(PlanPhase.RUNNING, [goal("g1", 0, [task("t1")])]))
    chat = InMemoryChatStore()

    with pytest.raises(InvalidEditError):
        turn(env, chat, ScriptedReasoner(), "hello")
    assert chat.list("p1") == []  # rejected before anything was persisted


def test_replanning_commit_proposes_replan_intent_without_mutating_cycle(env_factory):
    env = env_factory()
    done_task = task("t1")
    done_task.start()
    done_task.complete(TaskResult(status="success", output="ok"))
    done_goal = goal("g1", 0, [done_task])
    done_goal.start()
    done_goal.complete()
    plan = plan_in(PlanPhase.REPLANNING)
    plan.status = PlanStatus.PAUSED
    plan.cycles = [
        Cycle(
            id="cycle-1",
            intent_proposal_id="intent-old",
            draft_id="draft-old",
            status=CycleStatus.ACTIVE,
            goals=[done_goal],
            started_at=env.clock.now(),
        )
    ]
    env.seed(plan)
    chat = InMemoryChatStore()
    reasoner = ScriptedReasoner(
        replies=[
            ReasonerReply(
                message="replan intent",
                intent=IntentCandidate(
                    normalized_brief="Harden the service.",
                    objective="Harden the completed service",
                ),
            )
        ]
    )

    result = turn(env, chat, reasoner, "now harden it", replanning=True)

    assert (result.committed, result.phase) == (True, PlanPhase.REPLANNING)
    stored = env.stored("p1")
    assert stored.iteration == 1
    assert stored.active_cycle is not None
    assert [g.id for g in stored.active_cycle.goals] == ["g1"]
    assert stored.intent_proposal is not None
    assert stored.intent_proposal.source_cycle_id == "cycle-1"
    assert reasoner.converse_calls[0][0] == "replanning"


def test_replan_from_blocked_cycle_settles_work_before_message(env_factory):
    env = env_factory()
    failed = task("failed")
    failed.fail("terminal failure")
    pending = task("pending", 1)
    running_goal = goal("g1", 0, [failed, pending])
    running_goal.start()
    cycle = Cycle(
        id="cycle-1",
        intent_proposal_id="intent-old",
        draft_id="draft-old",
        goals=[running_goal],
        started_at=env.clock.now(),
    )
    plan = plan_in(PlanPhase.RUNNING)
    plan.status = PlanStatus.BLOCKED
    plan.cycles = [cycle]
    plan.block = PlanBlock(
        id="block-1",
        kind="execution_failure",
        explanation="terminal failure",
        stage="implementation",
        goal_id="g1",
        task_id="failed",
        legal_resolutions=["retry_stage", "edit_task", "start_replan"],
        created_at=env.clock.now(),
    )
    env.seed(plan)
    request_replan("p1", env.uow)

    settled = env.stored("p1")
    # unfreeze #10: a blocked-cycle replan lands in the coherent WAITING replan
    # tuple, NOT the old invalid status=PAUSED + paused=False state.
    assert settled.phase == PlanPhase.REPLANNING
    assert settled.status == PlanStatus.WAITING
    assert settled.paused is False and settled.pause_requested is False
    assert settled.block is not None and settled.block.resolution == "start_replan"
    # unfreeze #11: replanning does NOT rewrite the still-active source cycle's
    # task outcomes (no SKIPPED poisoning of an active cyclic goal); they stay
    # frozen and are superseded only when the replacement cycle activates.
    assert [item.status for item in settled.active_cycle.goals[0].tasks] == [
        Status.FAILED,
        Status.PENDING,
    ]
    # stale current-planning artifacts are retired; the source cycle is retained.
    assert settled.intent_proposal is None
    assert settled.cycle_draft is None
    assert settled.review_gate is None
    assert settled.active_cycle is not None
    assert settled.activity == "replan_discovery"
    assert "resume" not in settled.legal_actions

    chat = InMemoryChatStore()
    reasoner = ScriptedReasoner(
        replies=[
            ReasonerReply(
                message="replan intent",
                intent=IntentCandidate(
                    normalized_brief="Harden the service.",
                    objective="Harden the service",
                ),
            )
        ]
    )
    result = turn(env, chat, reasoner, "harden it", replanning=True)
    assert result.committed is True
    assert result.phase == PlanPhase.REPLANNING


def test_request_replan_fails_loud_when_goal_block_forbids_start_replan(env_factory):
    """Code-review guard: request_replan must never assume "start_replan" is
    in an active goal block's legal_resolutions -- a block that forbids it
    (e.g. a stage that only permits retry_stage/edit_task) must raise
    InvalidEditError naming the goal and its stage/legal resolutions instead
    of silently resolving it (or skipping it) anyway."""
    env = env_factory()
    blocked_task = task("blocked-task")
    blocked_goal = goal("g1", 0, [blocked_task])
    cycle = Cycle(
        id="cycle-1",
        intent_proposal_id="intent-old",
        draft_id="draft-old",
        status=CycleStatus.ACTIVE,
        goals=[blocked_goal],
        started_at=env.clock.now(),
    )
    plan = plan_in(PlanPhase.RUNNING)
    plan.status = PlanStatus.RUNNING
    plan.cycles = [cycle]
    plan.goal_blocks = {
        "g1": PlanBlock(
            id="block-1",
            kind="execution_failure",
            explanation="agent binding is broken",
            stage="agent_binding",
            goal_id="g1",
            task_id="blocked-task",
            legal_resolutions=["retry_stage", "edit_task"],
            created_at=env.clock.now(),
        )
    }
    env.seed(plan)

    with pytest.raises(InvalidEditError) as excinfo:
        request_replan("p1", env.uow)

    assert excinfo.value.code == "INVALID_EDIT"
    assert "g1" in str(excinfo.value)
    assert "agent_binding" in str(excinfo.value)


def test_replanning_message_stays_legal_while_only_partially_blocked(env_factory):
    """Domain unfreeze #14: a per-goal block leaves the plan status RUNNING
    (not BLOCKED) when a sibling goal can still progress. _start_operation's
    REPLAN precondition must recognize that as still eligible for
    replanning_message -- not just the legacy BLOCKED/PAUSED/IDLE statuses --
    since goal_blocks always advertises "start_replan" as a legal resolution
    and a chat message is one legitimate way to act on it."""
    env = env_factory()
    blocked_task = task("blocked-task")
    blocked_goal = goal("g1", 0, [blocked_task])
    other_goal = goal("g2", 1, [task("other-task")])
    cycle = Cycle(
        id="cycle-1",
        intent_proposal_id="intent-old",
        draft_id="draft-old",
        status=CycleStatus.ACTIVE,
        goals=[blocked_goal, other_goal],
        started_at=env.clock.now(),
    )
    plan = plan_in(PlanPhase.RUNNING)
    plan.status = PlanStatus.RUNNING  # partially blocked, NOT the legacy BLOCKED
    plan.cycles = [cycle]
    plan.goal_blocks = {
        "g1": PlanBlock(
            id="block-1",
            kind="execution_failure",
            explanation="terminal failure",
            stage="implementation",
            goal_id="g1",
            task_id="blocked-task",
            legal_resolutions=["retry_stage", "edit_task", "start_replan"],
            created_at=env.clock.now(),
        )
    }
    env.seed(plan)
    assert env.stored("p1").status == PlanStatus.RUNNING
    assert "start_replan" in env.stored("p1").legal_actions

    chat = InMemoryChatStore()
    reasoner = ScriptedReasoner(replies=[ReasonerReply(message="what should we change?")])

    # Must NOT raise InvalidEditError("replan discovery requires settled
    # active-cycle work") -- that check is for the legacy all-terminal-tasks
    # eligibility path, which doesn't apply here (g2 is still mid-flight).
    result = turn(env, chat, reasoner, "g1 keeps failing", replanning=True)

    assert result.reply == "what should we change?"
    assert result.committed is False


# ---------------------------------------------------------------------------
# ARCHITECTURE — the no-LLM passthrough
# ---------------------------------------------------------------------------


def test_architecture_passthrough_advances_without_reasoner(env_factory):
    env = env_factory()
    goals = [goal("g1", 0, [task("t1")])]
    env.seed(plan_in(PlanPhase.ARCHITECTURE, goals))
    reasoner = ScriptedReasoner()  # any reasoner call would pop an empty script

    signal = asyncio.run(handler(reasoner, env).handle("p1", env.stored("p1"), env.uow))

    assert signal == Signal.CONTINUE
    stored = env.stored("p1")
    assert stored.phase == PlanPhase.ENRICHING
    assert [g.id for g in stored.goals] == ["g1"]  # the goal set is untouched
    assert stored.goals[0].tasks[0].id == "t1"
    assert env.outbox_types() == ["PhaseAdvanced"]
    assert reasoner.converse_calls == [] and reasoner.enriched_goal_ids == []


# ---------------------------------------------------------------------------
# ENRICHING — the JIT step (one goal per handle, checkpointed)
# ---------------------------------------------------------------------------


async def _drive_planning(env, planning, max_steps=10):
    signals = []
    for _ in range(max_steps):
        signal = await planning.handle("p1", env.stored("p1"), env.uow)
        signals.append(signal)
        if signal != Signal.CONTINUE:
            return signals
    raise AssertionError("planning did not converge")


def test_jit_populates_each_taskless_goal_then_binds(env_factory):
    env = env_factory()
    env.seed(
        plan_in(
            PlanPhase.ENRICHING,
            [
                goal("g1", 0),
                goal("g2", 1, [task("preset", 0)]),  # user-authored: must be skipped
                goal("g3", 2),
            ],
        )
    )
    reasoner = ScriptedReasoner(
        task_sets={
            "g1": [task("g1-a"), task("g1-b")],
            "g3": [task("g3-a")],
        }
    )

    signals = asyncio.run(_drive_planning(env, handler(reasoner, env)))

    # one CONTINUE checkpoint per populated goal, then the binding PAUSED
    assert signals == [Signal.CONTINUE, Signal.CONTINUE, Signal.PAUSED]
    assert reasoner.enriched_goal_ids == ["g1", "g3"]  # position order, g2 skipped
    stored = env.stored("p1")
    assert stored.phase == PlanPhase.AWAITING_REVIEW
    by_id = {g.id: g for g in stored.goals}
    assert [(t.name, t.position) for t in by_id["g1"].tasks] == [("g1-a", 0), ("g1-b", 1)]
    assert [t.name for t in by_id["g2"].tasks] == ["preset"]  # untouched
    assert [t.name for t in by_id["g3"].tasks] == ["g3-a"]
    assert all(t.agent_id == "a1" for g in stored.goals for t in g.tasks)


def test_jit_idempotency_guard_never_enriches_twice(env_factory):
    """The crash window: the LLM produced tasks but a racer (another worker
    finishing the same claim after a lease expiry) committed first. The re-find
    inside the transaction sees the populated goal and drops the duplicate."""
    env = env_factory()
    env.seed(plan_in(PlanPhase.ENRICHING, [goal("g1", 0)]))

    def racer_commits_first():
        with env.uow:
            plan = env.uow.plans.get("p1")
            goals = [g.model_copy(deep=True) for g in plan.goals if not g.is_terminal]
            goals[0].tasks = [task("racer-task")]
            plan.set_iteration_goals(goals)
            plan.bump_version()
            env.uow.plans.save(plan)

    reasoner = ScriptedReasoner(
        task_sets={"g1": [task("late-duplicate")]},
        before_enrich=racer_commits_first,
    )

    signal = asyncio.run(handler(reasoner, env).handle("p1", env.stored("p1"), env.uow))

    assert signal == Signal.CONTINUE
    (g1,) = env.stored("p1").goals
    assert [t.name for t in g1.tasks] == ["racer-task"]  # the racer's commit won


def test_jit_phase_race_pauses_without_writing(env_factory):
    """A human command moved the plan out of ENRICHING while the LLM call was
    in flight: theirs wins, the handler writes nothing."""
    env = env_factory()
    env.seed(plan_in(PlanPhase.ENRICHING, [goal("g1", 0)]))

    def human_replans_meanwhile():
        with env.uow:
            plan = env.uow.plans.get("p1")
            plan.advance_phase(PlanPhase.DISCOVERY)  # any non-ENRICHING phase
            plan.bump_version()
            env.uow.plans.save(plan)

    reasoner = ScriptedReasoner(before_enrich=human_replans_meanwhile)

    signal = asyncio.run(handler(reasoner, env).handle("p1", env.stored("p1"), env.uow))

    assert signal == Signal.PAUSED
    (g1,) = env.stored("p1").goals
    assert g1.tasks == []  # nothing was written into the raced plan
