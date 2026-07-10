from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel

from src.domain.entities.agent_spec import AgentSpec
from src.domain.entities.goal import Goal
from src.domain.entities.task import Task
from src.domain.errors.planning_errors import PlanAlreadyTerminalError
from src.domain.errors.tasks_errors import InvalidTransitionError
from src.domain.policies.retry_policies import RetryPolicy
from src.domain.services.capability_matching import match_agent
from src.domain.services.lookups import find_goal, find_task
from src.domain.services.navigation import next_action, NextAction
from src.domain.value_objects.lifecycle import FailureKind, Status
from src.domain.value_objects.tasks_vos import TaskResult


class PlanPhase(str, Enum):
    """The nine-phase machine (see MASTER_ROADMAP_FINAL.md):

    DISCOVERY    — the first plan, reasoned from the brief (conversational, iter 1).
    REPLANNING   — conversational re-plan WITH the user, using prior iteration's
                   DONE results + chat. Reached from REVIEW ("replan") and from
                   mid-RUNNING chat (request_replan). Flows into ARCHITECTURE.
    ARCHITECTURE — structure the plan into an ordered roadmap of goals (worker).
    ENRICHING    — fill goal/task detail (worker; separate phase for crash-recovery
                   granularity).
    AWAITING_REVIEW — human gate BEFORE execution (approve / edit / send back).
    RUNNING      — execute the iteration's goals sequentially (the pull-scan core).
    REVIEW       — human gate AFTER execution: finish (-> DONE) or replan.
    DONE/FAILED  — terminal. DONE is reached ONLY from REVIEW "finish".
    """

    DISCOVERY = "discovery"
    REPLANNING = "replanning"
    ARCHITECTURE = "architecture"
    ENRICHING = "enriching"
    AWAITING_REVIEW = "awaiting_review"
    RUNNING = "running"
    REVIEW = "review"
    DONE = "done"
    FAILED = "failed"


TERMINAL_PHASES: frozenset[PlanPhase] = frozenset({PlanPhase.DONE, PlanPhase.FAILED})

# Driver model (roadmap r2): who advances each phase. Conversational phases are
# chat/API-driven; gates are human-command-driven; only these are worker-claimable.
WORKER_CLAIMABLE_PHASES: frozenset[PlanPhase] = frozenset(
    {PlanPhase.ARCHITECTURE, PlanPhase.ENRICHING, PlanPhase.RUNNING}
)

# The worker-driven planning phases where the reasoner runs. A permanent reasoner
# failure (or an exhausted retry budget) fails the plan FROM one of these.
WORKER_PLANNING_PHASES: frozenset[PlanPhase] = frozenset(
    {PlanPhase.ARCHITECTURE, PlanPhase.ENRICHING}
)


class Plan(BaseModel):
    """Aggregate root: owns the goal/task tree and is the ONLY caller of the
    entities' transition methods, so all invariants are enforced in one place.
    `version` is the optimistic lock. Navigation is delegated to the pure
    next_action scan — no stored cursor.

    The loop is append-only: each iteration's new goals are appended; prior DONE
    goals stay as history AND as context for the next re-plan. `iteration`
    distinguishes iteration N's goals; it increments when REPLANNING commits its
    new goal set (one defined point, not at request time)."""

    id: str
    version: int = 0
    brief: str
    phase: PlanPhase = PlanPhase.DISCOVERY
    iteration: int = 1
    retry_policy: RetryPolicy = RetryPolicy()
    goals: list[Goal] = []

    # Durable backoff gate for the worker-driven planning phases (the planning-phase
    # analog of a Task's retry_not_before + attempt). Armed on a TRANSIENT reasoner
    # failure and honored by the claim predicate, so a rate-limited provider makes
    # the worker back off instead of hot-looping it. planning_attempts is the
    # transient-failure counter; it resets when planning next progresses.
    planning_retry_not_before: datetime | None = None
    planning_attempts: int = 0

    # Human pause gate (un-freeze #3): an availability flag on the claim predicate
    # (the same durable-gate pattern as planning_retry_not_before), NOT a phase —
    # the nine-phase enum stays frozen. Armed by a human pause command or by the
    # auto-pause that replaced terminal goal failure; cleared by resume() (which
    # doubles as the manual retry) and by the phase-changing human commands.
    paused: bool = False
    paused_reason: str | None = None

    # ---- helpers ----
    def bump_version(self) -> None:
        self.version += 1

    def _assert_not_terminal(self) -> None:
        if self.phase in TERMINAL_PHASES:
            raise PlanAlreadyTerminalError(self.id, self.phase.value)

    def _guard_phase(self, allowed_from: set[PlanPhase], to: PlanPhase) -> None:
        if self.phase not in allowed_from:
            raise InvalidTransitionError("Plan", self.id, self.phase.value, to.value)

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

    def fail_task(
        self, goal_id: str, task_id: str, reason: str, kind: FailureKind | None = None
    ) -> None:
        self._assert_not_terminal()
        goal = self._goal(goal_id)
        self._task(goal, task_id).fail(reason, kind)

    def abandon_task(self, goal_id: str, task_id: str) -> None:
        """Tolerant finalize: terminal-skip an in-flight task whose iteration was
        abandoned by a replan — never requeue into an abandoned iteration."""
        self._assert_not_terminal()
        goal = self._goal(goal_id)
        self._task(goal, task_id).abandon()

    def reopen_task(self, goal_id: str, task_id: str) -> None:
        """Human-driven redo of a DONE task (review gate). The invariant (only a
        DONE task can be reopened) lives on Task.reopen(); exposing it here keeps
        the aggregate the single mutation root."""
        self._assert_not_terminal()
        goal = self._goal(goal_id)
        self._task(goal, task_id).reopen()
        # the goal may already be DONE; re-open it so the scan re-enters it
        if goal.status == Status.DONE:
            goal.reopen()

    # ---- goal transitions ----
    def complete_goal(self, goal_id: str) -> None:
        self._assert_not_terminal()
        self._goal(goal_id).complete()

    # ---- human pause gate (un-freeze #3) ----
    def pause(self, reason: str | None = None) -> None:
        """Arm the pause gate: the claim predicate skips a paused plan, so the
        worker stops at the next unit boundary (an in-flight attempt finalizes
        normally). Only meaningful in the worker-claimable phases — gates and
        conversational phases are already paused by the driver model. Idempotent
        when already paused (the reason may be refreshed)."""
        if self.paused:
            if reason is not None:
                self.paused_reason = reason
            return
        if self.phase not in WORKER_CLAIMABLE_PHASES:
            raise InvalidTransitionError("Plan", self.id, self.phase.value, "paused")
        self.paused = True
        self.paused_reason = reason

    def resume(self) -> list[str]:
        """Human resume — and the manual retry (decision #17): clear the pause
        gate and the planning backoff gate, return every FAILED task in a
        non-terminal goal to PENDING with a fresh attempt budget (bypassing
        should_retry by construction), and drop backoff gates on PENDING tasks so
        work restarts immediately. Returns the retried task ids (the caller emits
        PlanResumed)."""
        if not self.paused:
            raise InvalidTransitionError("Plan", self.id, self.phase.value, "resumed")
        self.paused = False
        self.paused_reason = None
        self.clear_planning_retry()
        retried: list[str] = []
        for goal in self.goals:
            if goal.is_terminal:
                continue
            for task in goal.tasks:
                if task.status == Status.FAILED:
                    task.retry()
                    retried.append(task.id)
                elif task.status == Status.PENDING and task.retry_not_before:
                    task.clear_backoff()
        return retried

    # ---- worker-driven planning retry gate (un-freeze 2026-07-08) ----
    def record_planning_retry(self, not_before: datetime | None) -> None:
        """A TRANSIENT reasoner failure in a worker-driven planning phase: bump the
        attempt counter and arm the durable backoff gate. The claim predicate skips
        an armed plan until `not_before`, so the worker backs off (durably, across
        crashes) instead of re-hitting the provider every poll."""
        self._assert_not_terminal()
        self.planning_attempts += 1
        self.planning_retry_not_before = not_before

    def clear_planning_retry(self) -> None:
        """Planning progressed — disarm the gate and reset the attempt counter."""
        self.planning_attempts = 0
        self.planning_retry_not_before = None

    def fail_plan(self) -> None:
        """Terminal reasoner failure from a worker-driven planning phase: the
        planning LLM is permanently unavailable or its retry budget is exhausted.
        ARCHITECTURE/ENRICHING -> FAILED. This is the ONLY remaining path to
        FAILED: a terminal *task* failure auto-pauses instead (un-freeze #3), so
        execution failures stay recoverable in-band."""
        self._guard_phase(set(WORKER_PLANNING_PHASES), PlanPhase.FAILED)
        self.phase = PlanPhase.FAILED

    # ---- phase transitions ----
    def advance_phase(self, to: PlanPhase) -> None:
        """Generic forward step used by the planning flow (DISCOVERY -> ARCHITECTURE
        -> ENRICHING -> AWAITING_REVIEW). The gate/loop transitions below are the
        guarded, named paths — prefer them wherever one exists."""
        self._assert_not_terminal()
        self.phase = to

    def approve(self) -> None:
        """Human approval at the pre-execution gate: AWAITING_REVIEW -> RUNNING."""
        self._guard_phase({PlanPhase.AWAITING_REVIEW}, PlanPhase.RUNNING)
        self.phase = PlanPhase.RUNNING

    def reopen_discovery(self) -> None:
        """Human "request changes" at the pre-execution gate: AWAITING_REVIEW ->
        DISCOVERY. Re-opens the planning conversation; the next commit flows
        through set_iteration_goals, which REPLACES the un-executed roadmap
        (terminal history is kept). Distinct from REPLANNING, whose commit
        appends and bumps the iteration — nothing has executed yet here, so
        there is no history worth preserving."""
        self._guard_phase({PlanPhase.AWAITING_REVIEW}, PlanPhase.DISCOVERY)
        self.paused = False
        self.paused_reason = None
        self.phase = PlanPhase.DISCOVERY

    def enter_review(self) -> None:
        """Execution exhausted the goal list: RUNNING -> REVIEW (the post-exec
        gate). DONE is reached only from REVIEW via finish_review()."""
        self._guard_phase({PlanPhase.RUNNING}, PlanPhase.REVIEW)
        self.phase = PlanPhase.REVIEW

    def finish_review(self) -> None:
        """Human "finish" at the post-execution gate: REVIEW -> DONE."""
        self._guard_phase({PlanPhase.REVIEW}, PlanPhase.DONE)
        self.phase = PlanPhase.DONE

    def begin_replanning(self) -> None:
        """Enter the conversational re-plan. Two entry points, one phase: from
        REVIEW ("replan next phase") or mid-RUNNING (user chat). Abandons the
        current iteration's remaining work: every PENDING goal (and any PENDING
        task inside a still-RUNNING goal) is SKIPPED now; an in-flight RUNNING
        task finalizes via tolerant finalize; whatever remains is closed by the
        finalize-abandon in commit_replanned_goals(). A human replan supersedes
        any pause: the gate clears so the committed roadmap can execute."""
        self._guard_phase({PlanPhase.RUNNING, PlanPhase.REVIEW}, PlanPhase.REPLANNING)
        self.paused = False
        self.paused_reason = None
        for goal in self.goals:
            if goal.status == Status.PENDING:
                for task in goal.tasks:
                    if task.status == Status.PENDING:
                        task.skip()
                goal.skip()
            elif goal.status == Status.RUNNING:
                for task in goal.tasks:
                    if task.status == Status.PENDING:
                        task.skip()
                # leave the goal RUNNING: its in-flight task finalizes tolerantly
        self.phase = PlanPhase.REPLANNING

    def set_iteration_goals(self, new_goals: list[Goal]) -> None:
        """The planning phases' write path (roadmap 2.5 driver): replace the
        current iteration's non-terminal goals with the reasoner's new set —
        DISCOVERY drafts them, ARCHITECTURE structures them, ENRICHING details
        them. Terminal goals (prior-iteration history) are never touched; the
        new set is renumbered to positions after them."""
        self._guard_phase(
            {PlanPhase.DISCOVERY, PlanPhase.ARCHITECTURE, PlanPhase.ENRICHING},
            self.phase,
        )
        kept = [g for g in self.goals if g.is_terminal]
        base = max((g.position for g in kept), default=-1) + 1
        for offset, goal in enumerate(new_goals):
            goal.position = base + offset
        self.goals = kept + list(new_goals)

    def commit_replanned_goals(self, new_goals: list[Goal]) -> None:
        """The conversational re-plan produced a new goal set: finalize-abandon
        whatever the prior iteration left non-terminal (closes the resurrection
        hole — a stale goal must never be re-executed after the next iteration
        starts), append the new goals (append-only history), bump the iteration,
        and flow into ARCHITECTURE like DISCOVERY does."""
        self._guard_phase({PlanPhase.REPLANNING}, PlanPhase.ARCHITECTURE)
        for goal in self.goals:
            if goal.is_terminal:
                continue
            for task in goal.tasks:
                if not task.is_terminal:
                    task.abandon()
            goal.skip()
        base = max((g.position for g in self.goals), default=-1) + 1
        for offset, goal in enumerate(new_goals):
            goal.position = base + offset
            self.goals.append(goal)
        self.iteration += 1
        self.phase = PlanPhase.ARCHITECTURE

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
