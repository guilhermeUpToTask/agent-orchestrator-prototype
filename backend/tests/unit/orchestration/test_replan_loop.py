"""The replan loop: REVIEW/mid-RUNNING -> REPLANNING -> ARCHITECTURE, the
two-place skip rule (skip-at-request + finalize-abandon-at-commit), the tolerant
finalize for late in-flight results, and the append-only iteration mechanics.
These are the regressions for the r2-verified resurrection bug."""

import asyncio
import pytest


from src.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from src.domain.entities.agent_spec import AgentSpec
from src.domain.entities.goal import Goal
from src.domain.entities.task import Task
from src.domain.errors.tasks_errors import InvalidTransitionError
from src.domain.policies.retry_policies import RetryPolicy
from src.domain.services.navigation import next_action
from src.domain.value_objects.lifecycle import Status
from src.domain.value_objects.tasks_vos import TaskResult

from src.app.use_cases.advance_plan import advance_plan
from src.app.use_cases.control import review_replan
from src.app.use_cases.request_replan import request_replan
from src.app.testing.fakes import (
    CollectingEventSink,
    DummyAgentRunner,
    DummyBehavior,
    FakeClock,
    InMemoryAgentRepository,
    InMemoryOutbox,
    InMemoryPlanRepository,
    InMemoryUnitOfWork,
    NoOpWorkspace,
)


def agent():
    return AgentSpec(
        id="a1",
        name="A",
        role="agent",
        model_role="agent",
        instructions="",
        default_retry=RetryPolicy(),
    )


def task(tid, pos=0, status=Status.PENDING):
    t = Task(id=tid, name=tid, position=pos, description="", agent_id="a1")
    t.status = status
    return t


def goal(gid, pos, tasks, status=Status.PENDING):
    g = Goal(id=gid, name=gid, position=pos, description="", tasks=tasks)
    g.status = status
    return g


def harness(plan, script=None):
    clock = FakeClock()
    repo = InMemoryPlanRepository(clock)
    repo.add(plan)
    outbox = InMemoryOutbox()
    uow = InMemoryUnitOfWork(repo, outbox)
    runner = DummyAgentRunner(script or {})
    agents = InMemoryAgentRepository([agent()], default_id="a1")
    return repo, outbox, uow, runner, agents, NoOpWorkspace(), CollectingEventSink(), clock


# ===== entering REPLANNING =====
def test_request_replan_mid_running_skips_pending_work():
    """Skip place ONE: at request time every PENDING goal (and PENDING tasks of a
    RUNNING goal) is skipped; the in-flight RUNNING task is left to finalize."""
    running_goal = goal(
        "g1",
        0,
        [task("g1t0", 0, Status.DONE), task("g1t1", 1, Status.RUNNING), task("g1t2", 2)],
        status=Status.RUNNING,
    )
    pending_goal = goal("g2", 1, [task("g2t0", 0)])
    plan = Plan(
        project_id="project-1",
        id="p1",
        brief="b",
        phase=PlanPhase.RUNNING,
        goals=[running_goal, pending_goal],
    )
    repo, outbox, uow, *_ = harness(plan)

    request_replan("p1", uow)
    saved = repo.get("p1")
    assert saved.phase == PlanPhase.REPLANNING
    assert saved.goals[1].status == Status.SKIPPED  # pending goal skipped
    assert saved.goals[1].tasks[0].status == Status.SKIPPED
    assert saved.goals[0].status == Status.RUNNING  # active goal left for finalize
    assert saved.goals[0].tasks[1].status == Status.RUNNING  # in-flight untouched
    assert saved.goals[0].tasks[2].status == Status.SKIPPED  # its pending work skipped
    assert "ReplanRequested" in outbox.types()


def test_review_replan_enters_replanning_from_review():
    plan = Plan(
        project_id="project-1",
        id="p1",
        brief="b",
        phase=PlanPhase.REVIEW,
        goals=[goal("g1", 0, [task("t0", 0, Status.DONE)], Status.DONE)],
    )
    repo, outbox, uow, *_ = harness(plan)
    review_replan("p1", uow)
    assert repo.get("p1").phase == PlanPhase.REPLANNING
    assert repo.get("p1").goals[0].status == Status.DONE  # history untouched


@pytest.mark.parametrize(
    "phase",
    [
        PlanPhase.DISCOVERY,
        PlanPhase.ARCHITECTURE,
        PlanPhase.ENRICHING,
        PlanPhase.AWAITING_REVIEW,
        PlanPhase.REPLANNING,
    ],
)
def test_replan_only_from_running_or_review(phase):
    plan = Plan(project_id="project-1", id="p1", brief="b", phase=phase)
    repo, outbox, uow, *_ = harness(plan)
    with pytest.raises(InvalidTransitionError):
        request_replan("p1", uow)


# ===== committing the new goal set =====
def test_commit_replanned_goals_appends_bumps_iteration_and_flows_to_architecture():
    done_goal = goal("g1", 0, [task("g1t0", 0, Status.DONE)], Status.DONE)
    plan = Plan(
        project_id="project-1", id="p1", brief="b", phase=PlanPhase.REVIEW, goals=[done_goal]
    )
    repo, outbox, uow, *_ = harness(plan)
    review_replan("p1", uow)

    new_goals = [
        Goal(id="n1", name="n1", position=0, description=""),
        Goal(id="n2", name="n2", position=1, description=""),
    ]
    with uow:
        p = uow.plans.get("p1")
        p.commit_replanned_goals(new_goals)
        p.bump_version()
        uow.plans.save(p)

    saved = repo.get("p1")
    assert saved.phase == PlanPhase.ARCHITECTURE
    assert saved.iteration == 2
    assert [g.id for g in saved.goals] == ["g1", "n1", "n2"]  # append-only
    assert [g.position for g in saved.goals] == [0, 1, 2]  # positions continue
    assert saved.goals[0].status == Status.DONE  # prior DONE goals are history


def test_finalize_abandon_closes_leftover_nonterminal_goals():
    """Skip place TWO (the resurrection fix): commit closes whatever the prior
    iteration left non-terminal, so the scan can never re-select stale work
    after the next iteration starts."""
    leftover = goal("g1", 0, [task("t0", 0, Status.RUNNING), task("t1", 1)], status=Status.RUNNING)
    plan = Plan(
        project_id="project-1", id="p1", brief="b", phase=PlanPhase.RUNNING, goals=[leftover]
    )
    repo, outbox, uow, *_ = harness(plan)
    request_replan("p1", uow)  # t1 skipped here; t0 still RUNNING (in flight)

    with uow:
        p = uow.plans.get("p1")
        p.commit_replanned_goals(
            [Goal(id="n1", name="n1", position=0, description="", tasks=[task("n1t0", 0)])]
        )
        p.bump_version()
        uow.plans.save(p)

    saved = repo.get("p1")
    assert saved.goals[0].status == Status.SKIPPED  # finalize-abandon closed it
    assert saved.goals[0].tasks[0].status == Status.SKIPPED  # in-flight abandoned
    # THE regression: the scan must select ONLY the new iteration's work
    action = next_action(saved.goals, FakeClock().now())
    picked_goal, picked_task = action
    assert picked_goal.id == "n1" and picked_task.id == "n1t0"


def test_commit_only_from_replanning():
    plan = Plan(project_id="project-1", id="p1", brief="b", phase=PlanPhase.RUNNING)
    with pytest.raises(InvalidTransitionError):
        plan.commit_replanned_goals([])


# ===== tolerant finalize: late in-flight results after a mid-RUNNING replan =====
class _ReplanDuringRun(DummyAgentRunner):
    """Simulates the user chat-triggering a replan WHILE the agent is executing:
    the replan lands between txn1 (task marked RUNNING) and the finalize txn."""

    def __init__(self, script, uow, trigger_task_id):
        super().__init__(script)
        self._uow = uow
        self._trigger = trigger_task_id

    async def run(self, task, spec, **kw):
        if task.id == self._trigger:
            request_replan("p1", self._uow)
        return await super().run(task, spec, **kw)


def _one_task_running_plan():
    return Plan(
        project_id="project-1",
        id="p1",
        brief="b",
        phase=PlanPhase.RUNNING,
        goals=[goal("g1", 0, [task("t0", 0)])],
    )


def test_late_failure_terminal_skips_never_requeues():
    repo, outbox, uow, _, agents, ws, sink, clock = harness(_one_task_running_plan())
    runner = _ReplanDuringRun(
        {"t0": DummyBehavior(always_fail=True, fail_reason="late boom")}, uow, "t0"
    )
    sig = asyncio.run(advance_plan("p1", uow, runner, agents, ws, sink, clock))
    assert sig == "paused"  # plan is in REPLANNING now
    saved = repo.get("p1")
    assert saved.goals[0].tasks[0].status == Status.SKIPPED  # terminal-skip
    assert "TaskRequeued" not in outbox.types()  # NEVER requeued into abandoned iter
    assert "TaskAbandoned" in outbox.types()


def test_late_success_is_rejected_and_abandoned():
    repo, outbox, uow, _, agents, ws, sink, clock = harness(_one_task_running_plan())
    runner = _ReplanDuringRun({"t0": DummyBehavior(output="late ok")}, uow, "t0")
    asyncio.run(advance_plan("p1", uow, runner, agents, ws, sink, clock))
    saved = repo.get("p1")
    assert saved.phase == PlanPhase.REPLANNING
    assert saved.goals[0].tasks[0].status == Status.SKIPPED
    assert saved.goals[0].tasks[0].result is None
    assert "TaskCompleted" not in outbox.types()
    assert "TaskAbandoned" in outbox.types()


def test_late_success_after_finalize_abandon_is_dropped():
    """The late-late edge: the conversational replan COMMITTED (finalize-abandon
    closed the task) before the agent returned. The stale success is dropped."""
    repo, outbox, uow, _, agents, ws, sink, clock = harness(_one_task_running_plan())

    class _ReplanAndCommit(_ReplanDuringRun):
        async def run(self, task, spec, **kw):
            request_replan("p1", self._uow)
            with self._uow:
                p = self._uow.plans.get("p1")
                p.commit_replanned_goals([Goal(id="n1", name="n1", position=0, description="")])
                p.bump_version()
                self._uow.plans.save(p)
            return await DummyAgentRunner.run(self, task, spec, **kw)

    runner = _ReplanAndCommit({"t0": DummyBehavior(output="too late")}, uow, "t0")
    sig = asyncio.run(advance_plan("p1", uow, runner, agents, ws, sink, clock))
    assert sig == "paused"
    saved = repo.get("p1")
    assert saved.goals[0].tasks[0].status == Status.SKIPPED  # stays closed
    assert saved.goals[0].tasks[0].result is None  # stale result NOT recorded
    assert "TaskCompleted" not in outbox.types()


# ===== reopen (human redo) through the aggregate =====
def test_reopen_task_reopens_goal_and_scan_reselects():
    done = goal("g1", 0, [task("t0", 0, Status.DONE)], Status.DONE)
    done.tasks[0].result = TaskResult.success("v1")
    plan = Plan(project_id="project-1", id="p1", brief="b", phase=PlanPhase.REVIEW, goals=[done])

    plan.reopen_task("g1", "t0")
    assert plan.goals[0].status == Status.RUNNING
    t = plan.goals[0].tasks[0]
    assert t.status == Status.PENDING and t.result is None and t.reopen_count == 1
    picked_goal, picked_task = next_action(plan.goals, FakeClock().now())
    assert picked_task.id == "t0"  # scan re-selects the reopened work


# ===== set_iteration_goals (the planning phases' write path) =====
def test_set_iteration_goals_replaces_pending_keeps_history():
    history = goal("old", 0, [task("oldt", 0, Status.DONE)], Status.DONE)
    draft = goal("draft", 1, [task("draftt", 0)])
    plan = Plan(
        project_id="project-1",
        id="p1",
        brief="b",
        phase=PlanPhase.ARCHITECTURE,
        goals=[history, draft],
    )
    plan.set_iteration_goals(
        [
            Goal(id="n1", name="n1", position=0, description=""),
            Goal(id="n2", name="n2", position=1, description=""),
        ]
    )
    assert [g.id for g in plan.goals] == ["old", "n1", "n2"]  # draft replaced
    assert [g.position for g in plan.goals] == [0, 1, 2]  # renumbered after history
    assert plan.goals[0].status == Status.DONE  # history untouched


@pytest.mark.parametrize(
    "phase",
    [
        PlanPhase.AWAITING_REVIEW,
        PlanPhase.RUNNING,
        PlanPhase.REVIEW,
        PlanPhase.REPLANNING,
        PlanPhase.DONE,
    ],
)
def test_set_iteration_goals_only_in_planning_phases(phase):
    plan = Plan(project_id="project-1", id="p1", brief="b", phase=phase)
    with pytest.raises((InvalidTransitionError, Exception)):
        plan.set_iteration_goals([])
