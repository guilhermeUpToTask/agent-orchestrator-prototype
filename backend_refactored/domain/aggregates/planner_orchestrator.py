from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel

from domain.entities.agent_spec import AgentSpec
from domain.entities.goal import Goal
from domain.entities.task import Task
from domain.errors.planning_errors import PlanAlreadyTerminalError
from domain.policies.retry_policies import RetryPolicy
from domain.services.capability_matching import match_agent
from domain.services.lookups import find_goal, find_task
from domain.services.navigation import next_action, NextAction
from domain.value_objects.tasks_vos import Status, TaskResult


class PlanPhase(str, Enum):
    DRAFTING = "drafting"
    BREAKDOWN = "breakdown"
    ENRICHING = "enriching"
    AWAITING_REVIEW = "awaiting_review"
    EXECUTING = "executing"
    DONE = "done"
    FAILED = "failed"


TERMINAL_PHASES: frozenset[PlanPhase] = frozenset({PlanPhase.DONE, PlanPhase.FAILED})


class Plan(BaseModel):
    """Aggregate root: owns the goal/task tree and is the ONLY caller of the
    entities' transition methods, so all invariants are enforced in one place.
    `version` is the optimistic lock. Navigation is delegated to the pure
    next_action scan — no stored cursor."""

    id: str
    version: int = 0
    brief: str
    phase: PlanPhase = PlanPhase.DRAFTING
    retry_policy: RetryPolicy = RetryPolicy()
    # Phases after which the worker pauses for human review before continuing.
    # Overlaps with PlanPhase.AWAITING_REVIEW — see ../../DESIGN_NOTES.md (human-review gate).
    pause_after: set[PlanPhase] = {PlanPhase.ENRICHING}
    goals: list[Goal] = []

    # ---- helpers ----
    def bump_version(self) -> None:
        self.version += 1

    def _assert_not_terminal(self) -> None:
        if self.phase in TERMINAL_PHASES:
            raise PlanAlreadyTerminalError(self.id, self.phase.value)

    def _goal(self, goal_id: str) -> Goal:
        return find_goal(self.goals, goal_id)

    def _task(self, goal: Goal, task_id: str) -> Task:
        return find_task(goal, task_id)

    # ---- navigation ----
    def peek_next(self, now: datetime) -> NextAction:
        return next_action(self.goals, now)

    # ---- task transitions (aggregate calls entity methods) ----
    def start_task(self, goal_id: str, task_id: str) -> None:
        self._assert_not_terminal()
        goal = self._goal(goal_id)
        task = self._task(goal, task_id)
        if task.result is not None:
            return  # idempotency: work already happened, do not restart
        if goal.status == Status.PENDING:
            goal.start()
        task.start()

    def complete_task(self, goal_id: str, task_id: str, result: TaskResult) -> None:
        self._assert_not_terminal()
        goal = self._goal(goal_id)
        self._task(goal, task_id).complete(result)

    def requeue_task(
        self, goal_id: str, task_id: str, not_before: datetime | None = None
    ) -> None:
        self._assert_not_terminal()
        goal = self._goal(goal_id)
        self._task(goal, task_id).requeue(not_before)

    def fail_task(self, goal_id: str, task_id: str, reason: str) -> None:
        self._assert_not_terminal()
        goal = self._goal(goal_id)
        self._task(goal, task_id).fail(reason)

    # ---- goal transitions ----
    def complete_goal(self, goal_id: str) -> None:
        self._assert_not_terminal()
        self._goal(goal_id).complete()

    def fail_goal(self, goal_id: str) -> None:
        """Goal-failure policy: a failed goal HALTS the plan (safe default).
        Skip-and-continue would be a future configurable knob."""
        self._goal(goal_id).fail()
        self.phase = PlanPhase.FAILED

    # ---- phase transitions ----
    def advance_phase(self, to: PlanPhase) -> None:
        self._assert_not_terminal()
        self.phase = to

    # Cooperative pause: checked by the worker loop between task units, never mid-run.
    def should_pause(self) -> bool:
        return self.phase in self.pause_after

    def mark_done(self) -> None:
        self.phase = PlanPhase.DONE

    # ---- creation-time agent binding ----
    def bind_agents(self, agents: list[AgentSpec], default_agent_id: str) -> list[str]:
        """Bind unbound tasks to agents by capability. Returns task ids that fell
        back to the default (caller emits AgentFellBackToDefault events)."""
        fell_back: list[str] = []
        for goal in self.goals:
            for task in goal.tasks:
                if task.agent_id is None:
                    agent_id, used_default = match_agent(
                        task.required_capabilities, agents, default_agent_id
                    )
                    task.agent_id = agent_id
                    if used_default:
                        fell_back.append(task.id)
        return fell_back
