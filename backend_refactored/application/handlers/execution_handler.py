"""ExecutionHandler — owns the RUNNING phase: the pull-scan task loop.

This is the crash-safety choreography (two-transaction write, check-before-act
idempotency, durable backoff gate, transactional outbox, retry-vs-terminal). It was
the core of the old advance_plan; extracted here so task execution is one isolated
concern that adding planning phases can never disturb.

Exhausting the scan transitions RUNNING -> REVIEW (the post-execution gate); DONE
is reached ONLY from REVIEW "finish". Late results landing after a mid-RUNNING
replan are handled by the TOLERANT FINALIZE: the finalize transactions re-check
plan.phase — a late failure terminal-skips (never requeues into an abandoned
iteration), a late success completes as harmless history unless the task was
already closed by the finalize-abandon.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from domain.aggregates.planner_orchestrator import Plan, PlanPhase
from domain.entities.agent_spec import AgentSpec
from domain.entities.goal import Goal
from domain.entities.task import Task
from domain.events.outbox import (
    GoalCompleted,
    GoalFailedEvent,
    PhaseAdvanced,
    PlanFailed,
    TaskAbandoned,
    TaskCompleted,
    TaskFailedEvent,
    TaskRequeued,
    TaskStarted,
)
from domain.policies.retry_policies import RetryPolicy
from domain.repositories.agent_repo import AgentRepository
from domain.services.lookups import find_goal, find_task
from domain.services.navigation import NOT_READY
from domain.value_objects.tasks_vos import TaskResult

from application.handlers.base import Signal
from application.ports import (
    AgentEventSink,
    AgentRunner,
    Clock,
    TaskFailed,
    UnitOfWork,
    Workspace,
)


@dataclass(frozen=True)
class _Unit:
    """Plain values captured inside txn1 — never live aggregate refs across the
    transaction boundary."""

    goal_id: str
    task_id: str
    attempt: int
    retry_policy: RetryPolicy
    task_snapshot: Task
    spec: AgentSpec


class ExecutionHandler:
    def __init__(
        self,
        runner: AgentRunner,
        agents: AgentRepository,
        workspace: Workspace,
        event_sink: AgentEventSink,
        clock: Clock,
    ) -> None:
        self._runner = runner
        self._agents = agents
        self._workspace = workspace
        self._event_sink = event_sink
        self._clock = clock

    async def handle(self, plan_id: str, plan: Plan, uow: UnitOfWork) -> Signal:
        # ---- txn1: pick the unit, mark RUNNING, persist + outbox atomically ----
        with uow:
            plan = uow.plans.get(plan_id)
            action = plan.peek_next(self._clock.now())

            if action is None:
                return self._enter_review(plan_id, plan, uow)
            if action == NOT_READY:
                return Signal.NOT_READY

            goal, second = action
            if second is None:  # goal's tasks all terminal, none failed -> close it
                return self._close_goal(plan_id, plan, goal, uow)
            if second == "GOAL_FAILED":
                return self._fail_goal(plan_id, plan, goal, uow)

            task = second
            # check-before-act: result already exists -> finalize without re-running.
            if task.result is not None:
                return self._finalize_existing(plan_id, plan, goal, task, uow)

            unit = self._start_unit(plan_id, plan, goal, task, uow)

        # ---- side effect OUTSIDE any transaction. Runner just executes — no policy. ----
        key = f"{plan_id}:{unit.goal_id}:{unit.task_id}"
        handle = await self._workspace.begin(plan_id, unit.task_id, unit.attempt)
        try:
            result: TaskResult = await self._runner.run(
                unit.task_snapshot,
                unit.spec,
                idempotency_key=key,
                event_sink=self._event_sink,
                workspace=handle,
            )
            await self._workspace.commit(handle)
        except TaskFailed as exc:
            await self._workspace.discard(handle)
            return self._finalize_failure(plan_id, unit, exc, uow)

        return self._finalize_success(plan_id, unit, result, uow)

    # ---- txn1 steps (called INSIDE the open transaction) ----

    def _enter_review(self, plan_id: str, plan: Plan, uow: UnitOfWork) -> Signal:
        """Scan exhausted: RUNNING -> REVIEW (post-exec gate), then pause for the
        human. PlanCompleted is emitted by the REVIEW->DONE transition, not here."""
        plan.enter_review()
        plan.bump_version()
        uow.outbox.add(
            PhaseAdvanced(
                plan_id=plan_id,
                from_phase=PlanPhase.RUNNING.value,
                to_phase=PlanPhase.REVIEW.value,
            )
        )
        uow.plans.save(plan)
        return Signal.PAUSED

    def _close_goal(
        self, plan_id: str, plan: Plan, goal: Goal, uow: UnitOfWork
    ) -> Signal:
        plan.complete_goal(goal.id)
        plan.bump_version()
        uow.outbox.add(GoalCompleted(plan_id=plan_id, goal_id=goal.id))
        uow.plans.save(plan)
        return Signal.CONTINUE

    def _fail_goal(
        self, plan_id: str, plan: Plan, goal: Goal, uow: UnitOfWork
    ) -> Signal:
        plan.fail_goal(goal.id)
        plan.bump_version()
        uow.outbox.add(GoalFailedEvent(plan_id=plan_id, goal_id=goal.id))
        uow.outbox.add(PlanFailed(plan_id=plan_id, reason=f"goal {goal.id} failed"))
        uow.plans.save(plan)
        return Signal.FAILED

    def _finalize_existing(
        self, plan_id: str, plan: Plan, goal: Goal, task: Task, uow: UnitOfWork
    ) -> Signal:
        """Check-before-act idempotency: the work already happened (crash between
        agent return and finalize) — record it without re-invoking the agent."""
        assert task.result is not None
        plan.complete_task(goal.id, task.id, task.result)
        plan.bump_version()
        uow.outbox.add(TaskCompleted(plan_id=plan_id, goal_id=goal.id, task_id=task.id))
        uow.plans.save(plan)
        return Signal.CONTINUE

    def _start_unit(
        self, plan_id: str, plan: Plan, goal: Goal, task: Task, uow: UnitOfWork
    ) -> _Unit:
        # resolve agent BEFORE marking running so a missing agent fails fast.
        spec = (
            self._agents.get(task.agent_id)
            if task.agent_id
            else self._agents.get(self._agents.default_agent_id())
        )
        plan.start_task(goal.id, task.id)  # RUNNING, attempts++, clears retry gate
        unit = _Unit(
            goal_id=goal.id,
            task_id=task.id,
            attempt=task.attempt,
            retry_policy=plan.retry_policy.model_copy(deep=True),
            task_snapshot=task.model_copy(deep=True),
            spec=spec,
        )
        plan.bump_version()
        uow.outbox.add(
            TaskStarted(
                plan_id=plan_id,
                goal_id=unit.goal_id,
                task_id=unit.task_id,
                attempt=unit.attempt,
            )
        )
        uow.plans.save(plan)
        return unit

    # ---- finalize transactions (each opens its own txn) ----

    def _abandoned_task_status(self, plan: Plan, unit: _Unit) -> Task | None:
        """If the plan left RUNNING (mid-flight replan), return the task for the
        tolerant-finalize checks; None means the normal path applies."""
        if plan.phase == PlanPhase.RUNNING:
            return None
        return find_task(find_goal(plan.goals, unit.goal_id), unit.task_id)

    def _finalize_failure(
        self, plan_id: str, unit: _Unit, exc: TaskFailed, uow: UnitOfWork
    ) -> Signal:
        with uow:
            plan = uow.plans.get(plan_id)

            # TOLERANT FINALIZE: the iteration was abandoned while we ran — a late
            # failure terminal-skips; it must NEVER requeue into the abandoned
            # iteration (the resurrection bug).
            abandoned = self._abandoned_task_status(plan, unit)
            if abandoned is not None:
                if not abandoned.is_terminal:
                    plan.abandon_task(unit.goal_id, unit.task_id)
                    plan.bump_version()
                    uow.outbox.add(
                        TaskAbandoned(
                            plan_id=plan_id,
                            goal_id=unit.goal_id,
                            task_id=unit.task_id,
                            reason=exc.reason,
                        )
                    )
                    uow.plans.save(plan)
                return Signal.PAUSED

            if unit.retry_policy.should_retry(unit.attempt, exc.kind):
                delay = unit.retry_policy.backoff_for(unit.attempt + 1)
                not_before = (
                    self._clock.now() + timedelta(seconds=delay) if delay > 0 else None
                )
                plan.requeue_task(unit.goal_id, unit.task_id, not_before)
                plan.bump_version()
                uow.outbox.add(
                    TaskRequeued(
                        plan_id=plan_id,
                        goal_id=unit.goal_id,
                        task_id=unit.task_id,
                        attempt=unit.attempt,
                        reason=exc.reason,
                    )
                )
                uow.plans.save(plan)
            else:
                plan.fail_task(unit.goal_id, unit.task_id, exc.reason, exc.kind)
                plan.bump_version()
                uow.outbox.add(
                    TaskFailedEvent(
                        plan_id=plan_id,
                        goal_id=unit.goal_id,
                        task_id=unit.task_id,
                        reason=exc.reason,
                    )
                )
                uow.plans.save(plan)
        return Signal.CONTINUE

    def _finalize_success(
        self, plan_id: str, unit: _Unit, result: TaskResult, uow: UnitOfWork
    ) -> Signal:
        # ---- txn2: persist result + DONE atomically with the event ----
        with uow:
            plan = uow.plans.get(plan_id)

            # TOLERANT FINALIZE (success side): if the finalize-abandon already
            # closed this task, drop the late result (harmless — the iteration is
            # abandoned). If the task is still RUNNING, complete it normally:
            # a late success is harmless history for the next re-plan.
            abandoned = self._abandoned_task_status(plan, unit)
            if abandoned is not None and abandoned.is_terminal:
                return Signal.PAUSED

            plan.complete_task(unit.goal_id, unit.task_id, result)
            plan.bump_version()
            uow.outbox.add(
                TaskCompleted(
                    plan_id=plan_id, goal_id=unit.goal_id, task_id=unit.task_id
                )
            )
            uow.plans.save(plan)
        return Signal.CONTINUE
