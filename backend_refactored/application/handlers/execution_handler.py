"""ExecutionHandler — owns the RUNNING phase: the pull-scan task loop.

This is the crash-safety choreography (two-transaction write, check-before-act
idempotency, durable backoff gate, transactional outbox, retry-vs-terminal). It was
the core of the old advance_plan; extracted here so task execution is one isolated
concern that adding planning phases can never disturb.
"""

from __future__ import annotations

from datetime import timedelta

from domain.aggregates.planner_orchestrator import Plan
from domain.events.outbox import (
    GoalCompleted,
    GoalFailedEvent,
    PlanCompleted,
    PlanFailed,
    TaskCompleted,
    TaskFailedEvent,
    TaskRequeued,
    TaskStarted,
)
from domain.repositories.agent_repo import AgentRepository
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

    # TODO: we should break the steps in more funcions for the thas class and the handle calls it, solid and dry principles...
    async def handle(self, plan_id: str, plan: Plan, uow: UnitOfWork) -> Signal:
        # ---- txn1: pick the unit, mark RUNNING, persist + outbox atomically ----
        with uow:
            plan = uow.plans.get(plan_id)
            action = plan.peek_next(self._clock.now())

            if action is None:
                plan.mark_done()
                plan.bump_version()
                uow.outbox.add(PlanCompleted(plan_id=plan_id))
                uow.plans.save(plan)
                return Signal.DONE

            if action == NOT_READY:
                return Signal.NOT_READY
            # TODO: is this a good data structure? would be more clean if is a tuple or a dto with goal, task, goal failed? so we did not need to narrow it? if is a corcern add in the design decisions
            goal, second = action

            if second is None:  # goal's tasks all terminal, none failed -> close it
                plan.complete_goal(goal.id)
                plan.bump_version()
                uow.outbox.add(GoalCompleted(plan_id=plan_id, goal_id=goal.id))
                uow.plans.save(plan)
                return Signal.CONTINUE

            if second == "GOAL_FAILED":
                plan.fail_goal(goal.id)
                plan.bump_version()
                uow.outbox.add(GoalFailedEvent(plan_id=plan_id, goal_id=goal.id))
                uow.outbox.add(
                    PlanFailed(plan_id=plan_id, reason=f"goal {goal.id} failed")
                )
                uow.plans.save(plan)
                return Signal.FAILED

            task = second

            # check-before-act: result already exists -> finalize without re-running.
            if task.result is not None:
                plan.complete_task(goal.id, task.id, task.result)
                plan.bump_version()
                uow.outbox.add(
                    TaskCompleted(plan_id=plan_id, goal_id=goal.id, task_id=task.id)
                )
                uow.plans.save(plan)
                return Signal.CONTINUE

            # resolve agent BEFORE marking running so a missing agent fails fast.
            spec = (
                self._agents.get(task.agent_id)
                if task.agent_id
                else self._agents.get(self._agents.default_agent_id())
            )

            plan.start_task(goal.id, task.id)  # RUNNING, attempts++, clears retry gate
            # capture plain VALUES; never hold live aggregate refs across the txn.
            goal_id = goal.id
            task_id = task.id
            attempt = task.attempt
            retry_policy = plan.retry_policy.model_copy(deep=True)
            task_snapshot = task.model_copy(deep=True)
            plan.bump_version()
            uow.outbox.add(
                TaskStarted(
                    plan_id=plan_id, goal_id=goal_id, task_id=task_id, attempt=attempt
                )
            )
            uow.plans.save(plan)

        # ---- side effect OUTSIDE any transaction. Runner just executes — no policy. ----
        key = f"{plan_id}:{goal_id}:{task_id}"
        handle = await self._workspace.begin(plan_id, task_id, attempt)
        try:
            result: TaskResult = await self._runner.run(
                task_snapshot,
                spec,
                idempotency_key=key,
                event_sink=self._event_sink,
                workspace=handle,
            )
            await self._workspace.commit(handle)
        except TaskFailed as exc:
            await self._workspace.discard(handle)
            with uow:
                plan = uow.plans.get(plan_id)
                if retry_policy.should_retry(attempt, exc.reason):
                    delay = retry_policy.backoff_for(attempt + 1)
                    not_before = (
                        self._clock.now() + timedelta(seconds=delay)
                        if delay > 0
                        else None
                    )
                    plan.requeue_task(goal_id, task_id, not_before)
                    plan.bump_version()
                    uow.outbox.add(
                        TaskRequeued(
                            plan_id=plan_id,
                            goal_id=goal_id,
                            task_id=task_id,
                            attempt=attempt,
                            reason=exc.reason,
                        )
                    )
                    uow.plans.save(plan)
                else:
                    plan.fail_task(goal_id, task_id, exc.reason)
                    plan.bump_version()
                    uow.outbox.add(
                        TaskFailedEvent(
                            plan_id=plan_id,
                            goal_id=goal_id,
                            task_id=task_id,
                            reason=exc.reason,
                        )
                    )
                    uow.plans.save(plan)
            return Signal.CONTINUE

        # ---- txn2: persist result + DONE atomically with the event ----
        with uow:
            plan = uow.plans.get(plan_id)
            plan.complete_task(goal_id, task_id, result)
            plan.bump_version()
            uow.outbox.add(
                TaskCompleted(plan_id=plan_id, goal_id=goal_id, task_id=task_id)
            )
            uow.plans.save(plan)
        return Signal.CONTINUE
