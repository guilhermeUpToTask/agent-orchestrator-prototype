from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, model_validator

from src.domain.entities.agent_spec import AgentSpec
from src.domain.entities.goal import Goal
from src.domain.entities.planning_artifacts import (
    Cycle,
    CycleDraft,
    CycleStatus,
    IntentProposal,
    OutputDisposition,
    PlanBlock,
    PlanStatus,
    ProposalKind,
    ReviewGate,
    ReviewResolution,
)
from src.domain.entities.task import Task
from src.domain.errors.planning_errors import InvalidEditError, PlanAlreadyTerminalError
from src.domain.errors.tasks_errors import InvalidTransitionError
from src.domain.policies.retry_policies import RetryPolicy
from src.domain.services.capability_matching import match_agent
from src.domain.services.lookups import find_goal, find_task
from src.domain.services.navigation import (
    action_for_goal,
    next_action,
    NextAction,
    plan_can_progress,
)
from src.domain.value_objects.lifecycle import FailureKind, Status, TERMINAL
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
    # Long-lived project ownership and cyclic lifecycle.
    project_id: str | None = None
    status: PlanStatus = PlanStatus.WAITING
    cycles: list[Cycle] = []
    intent_proposal: IntentProposal | None = None
    cycle_draft: CycleDraft | None = None
    review_gate: ReviewGate | None = None
    block: PlanBlock | None = None
    # Per-goal blocks (domain unfreeze #13 — goal-level parallelism v2): every
    # execution-triggered PlanBlock already carries a goal_id
    # (planning_artifacts.py); an active cycle routes those into this dict
    # instead of the scalar `block` above, so one goal's failure never stops
    # an unrelated, independent sibling goal. The scalar `block` remains the
    # ONLY block surface for legacy (non-cyclic) plans and for the few
    # genuinely plan-wide block kinds that carry no goal_id (reasoner_failure
    # during ARCHITECTURE/ENRICHING, project_binding) — see open_block/
    # resolve_block. New field, additive, defaults empty: no persisted-JSON
    # migration shim needed (contrast goal_promotion_reservations above,
    # which replaced an existing field's shape).
    goal_blocks: dict[str, PlanBlock] = {}
    pause_requested: bool = False
    # Durable check-to-merge reservation, PER GOAL (domain unfreeze #12 —
    # goal-level parallelism, ADR-001): while a goal's slot is set, pause
    # requests may land, but lifecycle/artifact mutations that could
    # supersede that goal's in-flight candidate may not. Keyed by goal_id so
    # two goals' task attempts / promotions can be reserved concurrently
    # without contending on each other — was a single `str | None` scalar
    # before this unfreeze (see `_migrate_legacy_promotion_reservation`
    # below for the persisted-JSON shim).
    goal_promotion_reservations: dict[str, str] = {}
    legacy_phase: str | None = None
    legacy_mapped_status: PlanStatus | None = None
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
    # the legacy phase projection remains unchanged. Armed by a human pause command;
    # cleared by resume(), which is intentionally separate from targeted retry.
    paused: bool = False
    paused_reason: str | None = None

    @model_validator(mode="before")
    @classmethod
    def map_legacy_phase_status(cls, data: object) -> object:
        if not isinstance(data, dict) or "status" in data:
            return data
        raw_phase = data.get("phase", PlanPhase.DISCOVERY.value)
        phase = raw_phase.value if isinstance(raw_phase, PlanPhase) else str(raw_phase)
        mapped = {
            PlanPhase.DISCOVERY.value: PlanStatus.WAITING,
            PlanPhase.REPLANNING.value: PlanStatus.WAITING,
            PlanPhase.ARCHITECTURE.value: PlanStatus.RUNNING,
            PlanPhase.ENRICHING.value: PlanStatus.RUNNING,
            PlanPhase.AWAITING_REVIEW.value: PlanStatus.WAITING,
            PlanPhase.RUNNING.value: PlanStatus.RUNNING,
            PlanPhase.REVIEW.value: PlanStatus.WAITING,
            PlanPhase.DONE.value: PlanStatus.IDLE,
            PlanPhase.FAILED.value: PlanStatus.BLOCKED,
        }
        return {**data, "status": mapped.get(phase, PlanStatus.BLOCKED)}

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_promotion_reservation(cls, data: object) -> object:
        """Domain unfreeze #12: `promotion_reservation: str | None` (a single
        plan-wide scalar) became `goal_promotion_reservations: dict[str, str]`
        (per goal_id). A persisted plan written before this unfreeze carries
        the old key with a token of the form `goal:{cycle_id}:{goal_id}` (the
        only shape ever written, per `ExecutionHandler._reserve_goal_promotion`)
        or, for a task-attempt-in-flight reservation, an opaque execution id
        with NO parseable goal_id. Since the legacy field only ever guarded
        ONE thing plan-wide, and the goal_id it belongs to (when parseable) is
        the last colon-separated segment of the `goal:` token, migrate:
        - `goal:{cycle_id}:{goal_id}` -> `{goal_id: token}`
        - anything else non-null (an opaque execution id) -> dropped. A
          reservation surviving only in a stale snapshot with no recoverable
          goal_id is, by construction, for an attempt that was already
          abandoned by the time this plan is ever reconstructed again (the
          worker's own tolerant-finalize path re-validates identity on every
          read) — recreating it under an unknown key would leave a
          permanently-unreleasable phantom reservation, which is worse than
          dropping it.
        """
        if not isinstance(data, dict) or "goal_promotion_reservations" in data:
            return data
        legacy = data.get("promotion_reservation")
        if not legacy:
            return {**data, "goal_promotion_reservations": {}}
        parts = legacy.split(":")
        if len(parts) == 3 and parts[0] == "goal":
            return {**data, "goal_promotion_reservations": {parts[2]: legacy}}
        return {**data, "goal_promotion_reservations": {}}

    # ---- helpers ----
    def _set_phase(self, phase: PlanPhase) -> None:
        """Compatibility checkpoint while active behavior migrates to artifacts."""
        self.phase = phase
        self.status = {
            PlanPhase.DISCOVERY: PlanStatus.WAITING,
            PlanPhase.REPLANNING: PlanStatus.WAITING,
            PlanPhase.ARCHITECTURE: PlanStatus.RUNNING,
            PlanPhase.ENRICHING: PlanStatus.RUNNING,
            PlanPhase.AWAITING_REVIEW: PlanStatus.WAITING,
            PlanPhase.RUNNING: PlanStatus.RUNNING,
            PlanPhase.REVIEW: PlanStatus.WAITING,
            PlanPhase.DONE: PlanStatus.IDLE,
            PlanPhase.FAILED: PlanStatus.BLOCKED,
        }[phase]

    def bump_version(self) -> None:
        self.version += 1

    def _assert_not_terminal(self) -> None:
        if self.phase in TERMINAL_PHASES:
            raise PlanAlreadyTerminalError(self.id, self.phase.value)

    def assert_lifecycle_mutation_allowed(self, goal_id: str | None = None) -> None:
        """`goal_id=None` (plan-wide mutations: replan, iteration commit, cycle
        draft edits) blocks if ANY goal has an open reservation — a plan-wide
        mutation legitimately must not race a live merge/finalize of any goal.
        `goal_id=<x>` (a goal-scoped mutation) blocks only on that goal's own
        reservation, so an in-flight task/promotion on goal A never blocks an
        edit that only touches goal B (domain unfreeze #12)."""
        if goal_id is None:
            if self.goal_promotion_reservations:
                raise InvalidEditError("a verified Git promotion is in progress")
            return
        if goal_id in self.goal_promotion_reservations:
            raise InvalidEditError("a verified Git promotion is in progress")

    def reserve_promotion(self, goal_id: str, reservation: str) -> None:
        current = self.goal_promotion_reservations.get(goal_id)
        if current not in (None, reservation):
            raise InvalidEditError("another verified Git promotion is in progress")
        self.goal_promotion_reservations[goal_id] = reservation

    def release_promotion(self, goal_id: str, reservation: str) -> None:
        if self.goal_promotion_reservations.get(goal_id) == reservation:
            del self.goal_promotion_reservations[goal_id]

    def abandon_execution_task(
        self,
        cycle_id: str | None,
        goal_id: str,
        task_id: str,
    ) -> None:
        goals = self.goals
        if cycle_id is not None:
            cycle = next((item for item in self.cycles if item.id == cycle_id), None)
            if cycle is None:
                raise InvalidEditError(f"cycle '{cycle_id}' not found")
            goals = cycle.goals
        find_task(find_goal(goals, goal_id), task_id).abandon()

    def _guard_phase(self, allowed_from: set[PlanPhase], to: PlanPhase) -> None:
        if self.phase not in allowed_from:
            raise InvalidTransitionError("Plan", self.id, self.phase.value, to.value)

    @property
    def execution_goals(self) -> list[Goal]:
        cycle = self.active_cycle
        return cycle.goals if cycle is not None else self.goals

    def _goal(self, goal_id: str) -> Goal:
        return find_goal(self.execution_goals, goal_id)

    def _task(self, goal: Goal, task_id: str) -> Task:
        return find_task(goal, task_id)

    # ---- navigation ----
    def peek_next(self, now: datetime) -> NextAction:
        return next_action(self.execution_goals, now)

    def peek_next_for_goal(self, goal_id: str, now: datetime) -> NextAction:
        """Goal-level parallelism (ADR-001, domain unfreeze #12): the caller
        (a goal-scoped worker, already holding that goal's lease) has already
        selected which goal to drive — this does NOT re-derive goal selection
        or dependency readiness the way `peek_next` does; it only computes
        the per-goal action for the ONE goal the caller names.

        Domain unfreeze #13 (symmetric per-goal leases): a goal that is
        already terminal (typically just-promoted DONE, since `drive_goal`'s
        loop calls this again immediately after a promotion returns CONTINUE)
        returns None -- the same "nothing left" signal `next_action` gives
        for a plan with no non-terminal goal at all. Without this check,
        `action_for_goal` would blindly re-derive "close it" for an
        already-DONE goal (its tasks are still DONE-with-evidence, so
        `can_promote_goal` would pass again) and attempt to re-promote/
        re-merge a goal that's already merged. Callers MUST NOT treat this
        None the same as `next_action`'s plan-wide None (see
        ExecutionHandler.handle's goal_id branch)."""
        goal = self._goal(goal_id)
        if goal.status in TERMINAL:
            return None
        return action_for_goal(goal, now)

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

    def requeue_task(self, goal_id: str, task_id: str, not_before: datetime | None = None) -> None:
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
        self._recompute_cyclic_status(datetime.now(timezone.utc))

    def _recompute_cyclic_status(self, now: datetime) -> None:
        """Domain unfreeze #13: re-derive whether a CYCLIC plan can still make
        progress, after a mutation that could change it (a goal block opened
        or resolved, or a goal completed). Legacy (non-cyclic) plans are
        untouched — they have no `goal_blocks` concept.

        Zero non-terminal goals means the cycle just finished, NOT that it's
        stuck — that case belongs to the "all goals terminal -> enter review"
        path in advance_plan.py, so it is short-circuited here rather than
        left to `plan_can_progress`'s vacuous-True-non-terminal-set case."""
        if self.active_cycle is None:
            return
        goals = self.execution_goals
        if not any(goal.status not in TERMINAL for goal in goals):
            return  # cycle just finished -- not stuck; advance_plan's own path handles it
        blocked_ids = {goal_id for goal_id, block in self.goal_blocks.items() if block.active}
        if not plan_can_progress(goals, blocked_ids, now):
            self.status = PlanStatus.BLOCKED
        elif self.status == PlanStatus.BLOCKED:
            self.status = PlanStatus.RUNNING

    # ---- graceful human pause gate ----
    def request_pause(self, active_action: bool, reason: str | None = None) -> None:
        """Block new claims immediately; settle only after an active action finalizes."""
        if self.paused or self.pause_requested:
            if reason is not None:
                self.paused_reason = reason
            return
        if self.status != PlanStatus.RUNNING or (
            self.active_cycle is None and self.phase not in WORKER_CLAIMABLE_PHASES
        ):
            raise InvalidTransitionError("Plan", self.id, self.status.value, "paused")
        self.paused_reason = reason
        if active_action:
            self.pause_requested = True
            return
        self.settle_pause()

    def settle_pause(self) -> None:
        """Transition a requested pause at an atomic action boundary."""
        self.pause_requested = False
        self.paused = True
        self.status = PlanStatus.PAUSED

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
        if self.active_cycle is None and self.phase not in WORKER_CLAIMABLE_PHASES:
            raise InvalidTransitionError("Plan", self.id, self.phase.value, "paused")
        self.paused = True
        self.paused_reason = reason
        self.status = PlanStatus.PAUSED

    def resume(self) -> None:
        """Remove a manual pause without mutating retry or backoff state."""
        if not self.paused:
            raise InvalidTransitionError("Plan", self.id, self.phase.value, "resumed")
        self.paused = False
        self.pause_requested = False
        self.paused_reason = None
        self.status = (
            PlanStatus.RUNNING
            if self.active_cycle is not None or self.phase in WORKER_CLAIMABLE_PHASES
            else PlanStatus.IDLE
        )

    def retry_task(self, goal_id: str, task_id: str, resolved_at: datetime) -> str | None:
        """Retry exactly one failed task; absolute attempt identity is preserved.

        Return the block resolution when retrying from a structured block. A
        legacy paused plan has no block and therefore returns None; callers must
        still issue the separate resume command to release its pause gate.

        Domain unfreeze #13: the relevant block may be `goal_blocks[goal_id]`
        (a cyclic plan's per-goal block) or the legacy plan-wide `self.block`
        — never both for the same goal. Resolving a per-goal block only
        re-derives THIS plan's overall status (other goals' blocks, if any,
        are untouched); resolving the legacy scalar keeps forcing RUNNING
        exactly as before.
        """
        goal_block = self.goal_blocks.get(goal_id) if self.active_cycle is not None else None
        per_goal_blocked = goal_block is not None and goal_block.active
        scalar_blocked = self.block is not None and self.block.active
        blocked = per_goal_blocked or scalar_blocked
        if not self.paused and not blocked:
            raise InvalidTransitionError("Plan", self.id, self.status.value, "retry")
        active_block = goal_block if per_goal_blocked else (self.block if scalar_blocked else None)
        resolution: str | None = None
        if active_block is not None and (
            active_block.goal_id != goal_id
            or active_block.task_id != task_id
            or not {"retry_stage", "wait_and_retry"}.intersection(active_block.legal_resolutions)
        ):
            raise InvalidEditError("retry does not target the active plan block")
        if active_block is not None:
            resolution = (
                "wait_and_retry"
                if "wait_and_retry" in active_block.legal_resolutions
                else "retry_stage"
            )
        goal = self._goal(goal_id)
        task = self._task(goal, task_id)
        edited_pending = bool(
            active_block is not None
            and task.status == Status.PENDING
            and "edit_task" in active_block.legal_resolutions
        )
        if edited_pending:
            resolution = "edit_task"
        else:
            task.retry()
        if active_block is not None:
            active_block.resolution = resolution
            active_block.resolved_at = resolved_at
            if per_goal_blocked:
                self._recompute_cyclic_status(resolved_at)
            else:
                self.status = PlanStatus.RUNNING
        return resolution

    def retry_planning_stage(self, resolved_at: datetime) -> None:
        """Resolve a cyclic reasoner failure and requeue the same planning stage."""
        if (
            self.block is None
            or not self.block.active
            or self.block.kind != "reasoner_failure"
            or "retry_stage" not in self.block.legal_resolutions
        ):
            raise InvalidEditError("plan is not blocked on a retryable planning stage")
        self.block.resolution = "retry_stage"
        self.block.resolved_at = resolved_at
        self.clear_planning_retry()
        self.status = PlanStatus.RUNNING

    def retry_agent_binding(
        self,
        goal_id: str,
        role_agent_ids_by_task: dict[str, dict[str, str]],
        resolved_at: datetime,
    ) -> None:
        """Atomically bind every frozen task after the user repairs the registry.

        Domain unfreeze #13: a goal-enrichment `agent_capability` block always
        carries `goal_id` and, going forward, routes into `goal_blocks` (see
        `open_block`) — checked first here, falling back to the legacy scalar
        `self.block` only for a block that was already open before this
        unfreeze deployed (never both for the same goal)."""
        goal_block = self.goal_blocks.get(goal_id) if self.active_cycle is not None else None
        per_goal = goal_block is not None and goal_block.active and goal_block.kind == "agent_capability"
        block = goal_block if per_goal else self.block
        if (
            block is None
            or not block.active
            or block.kind != "agent_capability"
            or block.goal_id != goal_id
            or "retry_stage" not in block.legal_resolutions
        ):
            raise InvalidEditError("plan is not blocked on retryable agent binding")
        goal = self._goal(goal_id)
        expected_task_ids = {task.id for task in goal.tasks}
        if not expected_task_ids or set(role_agent_ids_by_task) != expected_task_ids:
            raise InvalidEditError("agent bindings must cover every frozen task")
        required_roles = {"test_author", "implementer"}
        for task in goal.tasks:
            binding = role_agent_ids_by_task[task.id]
            if not required_roles.issubset(binding) or any(
                not binding[role] for role in required_roles
            ):
                raise InvalidEditError("each task requires test-author and implementer bindings")

        for task in goal.tasks:
            task.role_agent_ids = dict(role_agent_ids_by_task[task.id])
            task.agent_id = task.role_agent_ids["implementer"]
        block.resolution = "retry_stage"
        block.resolved_at = resolved_at
        self.clear_planning_retry()
        self._set_phase(PlanPhase.RUNNING)
        if per_goal:
            self._recompute_cyclic_status(resolved_at)

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
        self._set_phase(PlanPhase.FAILED)

    # ---- phase transitions ----
    def advance_phase(self, to: PlanPhase) -> None:
        """Generic forward step used by the planning flow (DISCOVERY -> ARCHITECTURE
        -> ENRICHING -> AWAITING_REVIEW). The gate/loop transitions below are the
        guarded, named paths — prefer them wherever one exists."""
        self._assert_not_terminal()
        self._set_phase(to)

    def approve(self) -> None:
        """Human approval at the pre-execution gate: AWAITING_REVIEW -> RUNNING."""
        self._guard_phase({PlanPhase.AWAITING_REVIEW}, PlanPhase.RUNNING)
        self._set_phase(PlanPhase.RUNNING)

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
        self._set_phase(PlanPhase.DISCOVERY)

    def enter_review(self) -> None:
        """Execution exhausted the goal list: RUNNING -> REVIEW (the post-exec
        gate). DONE is reached only from REVIEW via finish_review()."""
        self._guard_phase({PlanPhase.RUNNING}, PlanPhase.REVIEW)
        self._set_phase(PlanPhase.REVIEW)

    def finish_review(self) -> None:
        """Human "finish" at the post-execution gate: REVIEW -> DONE."""
        self._guard_phase({PlanPhase.REVIEW}, PlanPhase.DONE)
        self._set_phase(PlanPhase.DONE)

    def begin_replanning(self) -> None:
        """Enter the conversational re-plan. Two entry points, one phase: from
        REVIEW ("replan next phase") or mid-RUNNING (user chat). Abandons the
        current iteration's remaining work: every PENDING goal (and any PENDING
        task inside a still-RUNNING goal) is SKIPPED now; an in-flight RUNNING
        task finalizes via tolerant finalize; whatever remains is closed by the
        finalize-abandon in commit_replanned_goals(). A human replan supersedes
        any pause: the gate clears so the committed roadmap can execute."""
        self.assert_lifecycle_mutation_allowed()
        # Cyclic authority: a plan with an active cycle can always be replanned
        # (start_replan is an advertised block/running resolution), regardless of
        # the legacy PlanPhase projection — which for a replanned cycle is already
        # REPLANNING and would otherwise reject "replanning -> replanning". Legacy
        # (pre-cyclic) plans keep the RUNNING/REVIEW phase guard.
        if self.active_cycle is None:
            self._guard_phase({PlanPhase.RUNNING, PlanPhase.REVIEW}, PlanPhase.REPLANNING)
        self.paused = False
        self.paused_reason = None
        self.pause_requested = False
        if self.active_cycle is None:
            # Legacy append-only loop: guard on the legacy phase and SKIP the
            # abandoned root work so the root-goal scan never resurrects the
            # superseded iteration. Unchanged behavior for pre-cyclic plans.
            self._guard_phase({PlanPhase.RUNNING, PlanPhase.REVIEW}, PlanPhase.REPLANNING)
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
            self._set_phase(PlanPhase.REPLANNING)
        else:
            # Cyclic conversational replan (unfreeze #10/#11): SKIP NOTHING. The
            # source Cycle stays frozen — SKIPPED is legacy iteration-abandonment
            # residue, invalid for an active cyclic goal — and is superseded only
            # when the replacement cycle activates. Establish the coherent WAITING
            # replan tuple: set the compat `phase` projection AND the authoritative
            # `status` explicitly (not via `_set_phase`'s dual write — issue #41),
            # and retire the stale current-planning artifacts so an approved source
            # intent / draft / gate cannot masquerade as active planning work.
            self.phase = PlanPhase.REPLANNING
            self.status = PlanStatus.WAITING
            self.intent_proposal = None
            self.cycle_draft = None
            self.review_gate = None

    def set_iteration_goals(self, new_goals: list[Goal]) -> None:
        """The planning phases' write path (roadmap 2.5 driver): replace the
        current iteration's non-terminal goals with the reasoner's new set —
        DISCOVERY drafts them, ARCHITECTURE structures them, ENRICHING details
        them. Terminal goals (prior-iteration history) are never touched; the
        new set is renumbered to positions after them."""
        self.assert_lifecycle_mutation_allowed()
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
        self.assert_lifecycle_mutation_allowed()
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
        self._set_phase(PlanPhase.ARCHITECTURE)

    def bind_legacy_project(self, project_id: str, resolved_at: datetime) -> None:
        """Operator-only binding for quarantined legacy rows."""
        if self.project_id is not None:
            if self.project_id == project_id:
                return
            raise InvalidEditError("project identity is immutable")
        if self.block is None or self.block.kind != "project_binding" or not self.block.active:
            raise InvalidEditError("plan is not waiting for a project binding")
        self.project_id = project_id
        self.block.resolution = "bind_project"
        self.block.resolved_at = resolved_at
        self.status = self.legacy_mapped_status or PlanStatus.IDLE

    # ---- cyclic project-plan lifecycle (unfreeze #4) ----
    @property
    def active_cycle(self) -> Cycle | None:
        return next(
            (cycle for cycle in self.cycles if cycle.status == CycleStatus.ACTIVE),
            None,
        )

    @property
    def _active_goal_blocks(self) -> list[PlanBlock]:
        """Domain unfreeze #13: every currently-active per-goal block,
        deterministically ordered oldest-first (used wherever a single
        representative block must be picked for a coarse top-level summary —
        the full per-goal detail is always available via `goal_blocks`)."""
        return sorted(
            (block for block in self.goal_blocks.values() if block.active),
            key=lambda block: block.created_at,
        )

    @property
    def status_reason(self) -> dict[str, str | None]:
        if self.goal_promotion_reservations:
            return {
                "kind": "promotion",
                "code": "git_promotion",
                "message": "Promoting verified code at an atomic boundary.",
            }
        if self.block is not None and self.block.active:
            # A plan-wide block is the headline, but never hide coexisting
            # per-goal blocks from the coarse summary -- operators would
            # otherwise only discover them after resolving the scalar one.
            message = self.block.explanation
            also_blocked = len(self._active_goal_blocks)
            if also_blocked:
                message = (
                    f"{message} ({also_blocked} goal(s) independently blocked; "
                    "see goal_blocks)"
                )
            return {
                "kind": "block",
                "code": self.block.kind,
                "message": message,
            }
        active_goal_blocks = self._active_goal_blocks
        if active_goal_blocks:
            if self.status == PlanStatus.BLOCKED:
                # Every non-terminal goal is blocked or depends on one that
                # is -- report the oldest block as representative, same shape
                # as the legacy scalar case above.
                earliest = active_goal_blocks[0]
                return {"kind": "block", "code": earliest.kind, "message": earliest.explanation}
            return {
                "kind": "partially_blocked",
                "code": None,
                "message": (
                    f"{len(active_goal_blocks)} goal(s) blocked; other goals continue."
                ),
            }
        if self.review_gate is not None and self.review_gate.unresolved:
            return {
                "kind": "review_gate",
                "code": self.review_gate.subject_type.value,
                "message": self.review_gate.continuation,
            }
        if self.pause_requested:
            return {
                "kind": "pause_requested",
                "code": "active_action",
                "message": self.paused_reason or "waiting for the current action",
            }
        if self.paused:
            return {
                "kind": "manual_pause",
                "code": "paused",
                "message": self.paused_reason,
            }
        return {"kind": self.status.value, "code": None, "message": None}

    @property
    def legal_actions(self) -> list[str]:
        if self.goal_promotion_reservations:
            return ["pause"] if self.status == PlanStatus.RUNNING else []
        if self.block is not None and self.block.active:
            # Same coexistence rule as status_reason: the scalar block's
            # resolutions lead, but per-goal blocks stay discoverable.
            actions = list(self.block.legal_resolutions)
            for goal_block in self._active_goal_blocks:
                for resolution in goal_block.legal_resolutions:
                    if resolution not in actions:
                        actions.append(resolution)
            return actions
        active_goal_blocks = self._active_goal_blocks
        if active_goal_blocks:
            union: list[str] = []
            for block in active_goal_blocks:
                for resolution in block.legal_resolutions:
                    if resolution not in union:
                        union.append(resolution)
            if self.status == PlanStatus.BLOCKED:
                return union
            # Partially blocked: still RUNNING overall, so pause/start_replan
            # remain legal too -- prepend the per-goal resolutions so callers
            # can discover them without inspecting goal_blocks separately.
            for extra in ("pause", "start_replan"):
                if extra not in union:
                    union.append(extra)
            return union
        if self.review_gate is not None and self.review_gate.unresolved:
            return [f"review:{decision}" for decision in self.review_gate.allowed_decisions]
        if self.pause_requested:
            return []
        if self.status == PlanStatus.RUNNING:
            return ["pause", "start_replan"]
        if self.status == PlanStatus.PAUSED and self.paused:
            # Only a truly-armed manual pause is resumable. status==PAUSED with
            # paused==False is an inconsistent state (unfreeze #10), not a
            # resumable one — never advertise resume for it.
            return [
                "resume",
                "start_replan",
                "edit_pending_work",
            ]
        if self.status == PlanStatus.IDLE or (
            self.status == PlanStatus.WAITING
            and self.review_gate is None
            and self.intent_proposal is None
            and self.active_cycle is None
        ):
            # start_intent is INITIAL planning only. A WAITING plan with an active
            # cycle is in conversational replan (driven by the replan-message
            # endpoint), so it advertises no button command here.
            return ["start_intent"]
        return []

    @property
    def activity(self) -> str:
        """Derived activity; never persisted as a second lifecycle enum."""
        if self.block is not None and self.block.active:
            return f"blocked:{self.block.stage}"
        active_goal_blocks = self._active_goal_blocks
        if active_goal_blocks:
            if self.status == PlanStatus.BLOCKED:
                return f"blocked:{active_goal_blocks[0].stage}"
            return "partially_blocked"
        if self.review_gate is not None and self.review_gate.unresolved:
            return f"review:{self.review_gate.subject_type.value}"
        if self.intent_proposal is not None and self.intent_proposal.approved_at is None:
            return "intent_discovery"
        if (
            self.intent_proposal is not None
            and self.intent_proposal.approved_at is not None
            and self.cycle_draft is None
        ):
            return "cycle_architecture"
        if self.cycle_draft is not None and self.cycle_draft.approved_at is None:
            return "cycle_architecture"
        if (
            self.status == PlanStatus.WAITING
            and self.phase == PlanPhase.REPLANNING
            and self.active_cycle is not None
            and self.intent_proposal is None
        ):
            # Cyclic conversational replan before a candidate exists (unfreeze #10):
            # the source cycle is retained but planning artifacts are cleared.
            return "replan_discovery"
        cycle = self.active_cycle
        if cycle is None:
            return "intent_discovery" if self.status == PlanStatus.WAITING else "idle"
        action = next_action(cycle.goals, datetime.max.replace(tzinfo=timezone.utc))
        if isinstance(action, tuple):
            goal, subject = action
            if isinstance(subject, Task):
                return f"task:{goal.id}:{subject.id}"
            return f"goal:{goal.id}"
        return "cycle_verification"

    def _open_review_gate(self, gate: ReviewGate) -> None:
        if self.review_gate is not None and self.review_gate.unresolved:
            raise InvalidEditError("a blocking review gate is already open")
        self.review_gate = gate
        self.status = PlanStatus.WAITING

    def _resolve_review_gate(
        self,
        gate_id: str,
        subject_revision: int,
        decision: str,
        resolved_at: datetime,
        resolved_by: str | None = None,
        note: str | None = None,
    ) -> None:
        gate = self.review_gate
        if gate is None or gate.id != gate_id:
            raise InvalidEditError("review gate not found")
        if gate.resolution is not None:
            if gate.resolution.decision == decision:
                return
            raise InvalidEditError("review gate was already resolved differently")
        if gate.invalidated_at is not None:
            raise InvalidEditError("review gate is invalidated")
        if gate.subject_revision != subject_revision:
            raise InvalidEditError("review decision targets a stale subject revision")
        if decision not in gate.allowed_decisions:
            raise InvalidEditError(f"review decision '{decision}' is not legal")
        gate.resolution = ReviewResolution(
            decision=decision,
            resolved_at=resolved_at,
            resolved_by=resolved_by,
            note=note,
        )

    def propose_intent(self, proposal: IntentProposal, gate: ReviewGate) -> None:
        self.assert_lifecycle_mutation_allowed()
        if self.project_id is None:
            raise InvalidEditError("project binding is required before intent planning")
        if self.intent_proposal is not None and self.intent_proposal.cancelled_at is None:
            raise InvalidEditError("an intent proposal is already open")
        if proposal.base_plan_version != self.version:
            raise InvalidEditError("intent proposal base version is stale")
        if proposal.kind == ProposalKind.REPLAN:
            if proposal.source_cycle_id != (
                self.active_cycle.id if self.active_cycle is not None else None
            ):
                raise InvalidEditError("replan source cycle is stale")
        self.intent_proposal = proposal
        self.cycle_draft = None
        self._open_review_gate(gate)

    def revise_intent(
        self,
        proposal: IntentProposal,
        replacement_gate: ReviewGate,
        invalidated_at: datetime,
    ) -> None:
        self.assert_lifecycle_mutation_allowed()
        current = self.intent_proposal
        if current is None or current.id != proposal.id:
            raise InvalidEditError("intent proposal not found")
        if proposal.revision != current.revision + 1:
            raise InvalidEditError("intent proposal revision must increment by one")
        if proposal.base_plan_version != self.version:
            raise InvalidEditError("intent proposal base version is stale")
        if proposal.kind != current.kind or proposal.source_cycle_id != current.source_cycle_id:
            raise InvalidEditError("intent kind and replan source are immutable")
        if self.review_gate is not None and self.review_gate.unresolved:
            self.review_gate.invalidated_at = invalidated_at
        self.review_gate = None
        self.intent_proposal = proposal
        self._open_review_gate(replacement_gate)

    def cancel_intent(self, cancelled_at: datetime) -> None:
        self.assert_lifecycle_mutation_allowed()
        proposal = self.intent_proposal
        if proposal is None or proposal.approved_at is not None:
            raise InvalidEditError("open intent proposal not found")
        proposal.cancelled_at = cancelled_at
        if self.review_gate is not None and self.review_gate.unresolved:
            self.review_gate.invalidated_at = cancelled_at
        self.intent_proposal = None
        self.review_gate = None
        self.status = PlanStatus.PAUSED if self.active_cycle is not None else PlanStatus.IDLE

    def approve_intent(self, gate_id: str, revision: int, resolved_at: datetime) -> None:
        self.assert_lifecycle_mutation_allowed()
        proposal = self.intent_proposal
        if proposal is None or proposal.revision != revision:
            raise InvalidEditError("intent proposal revision is stale")
        self._resolve_review_gate(gate_id, revision, "approve", resolved_at)
        proposal.approved_at = resolved_at
        self.status = PlanStatus.RUNNING

    def submit_cycle_draft(self, draft: CycleDraft, gate: ReviewGate) -> None:
        self.assert_lifecycle_mutation_allowed()
        proposal = self.intent_proposal
        if proposal is None or proposal.approved_at is None:
            raise InvalidEditError("an approved intent is required")
        if draft.intent_proposal_id != proposal.id:
            raise InvalidEditError("cycle draft references the wrong intent")
        if draft.base_plan_version != self.version:
            raise InvalidEditError("cycle draft base version is stale")
        if draft.source_cycle_id != proposal.source_cycle_id:
            raise InvalidEditError("cycle draft source cycle is stale")
        self.review_gate = None
        self.cycle_draft = draft
        self._open_review_gate(gate)

    def revise_cycle_draft(
        self,
        draft: CycleDraft,
        replacement_gate: ReviewGate,
        invalidated_at: datetime,
    ) -> None:
        self.assert_lifecycle_mutation_allowed()
        current = self.cycle_draft
        if current is None or current.id != draft.id:
            raise InvalidEditError("cycle draft not found")
        if draft.revision != current.revision + 1:
            raise InvalidEditError("cycle draft revision must increment by one")
        if draft.base_plan_version != self.version:
            raise InvalidEditError("cycle draft base version is stale")
        if (
            draft.intent_proposal_id != current.intent_proposal_id
            or draft.source_cycle_id != current.source_cycle_id
        ):
            raise InvalidEditError("cycle draft intent and source are immutable")
        if self.review_gate is not None and self.review_gate.unresolved:
            self.review_gate.invalidated_at = invalidated_at
        self.review_gate = None
        self.cycle_draft = draft
        self._open_review_gate(replacement_gate)

    def activate_cycle(
        self,
        gate_id: str,
        revision: int,
        cycle: Cycle,
        resolved_at: datetime,
    ) -> None:
        self.assert_lifecycle_mutation_allowed()
        draft = self.cycle_draft
        if draft is None or draft.revision != revision:
            raise InvalidEditError("cycle draft revision is stale")
        if self.version != draft.base_plan_version + 1:
            raise InvalidEditError("cycle draft base version is stale")
        if cycle.draft_id != draft.id or cycle.intent_proposal_id != draft.intent_proposal_id:
            raise InvalidEditError("cycle does not match the approved draft")
        source = self.active_cycle
        if draft.source_cycle_id is not None:
            if source is None or source.id != draft.source_cycle_id:
                raise InvalidEditError("active source cycle changed; regenerate the draft")
            source.status = CycleStatus.SUPERSEDED
            source.superseded_at = resolved_at
        elif source is not None:
            raise InvalidEditError("only one active cycle is allowed")
        self._resolve_review_gate(gate_id, revision, "approve", resolved_at)
        draft.approved_at = resolved_at
        self.cycles.append(cycle)
        self.intent_proposal = None
        self.cycle_draft = None
        self.review_gate = None
        self.block = None
        self.goal_blocks = {}
        self.paused = False
        self.pause_requested = False
        self.status = PlanStatus.RUNNING

    def cancel_cycle_draft(self, cancelled_at: datetime) -> None:
        self.assert_lifecycle_mutation_allowed()
        if self.cycle_draft is None:
            raise InvalidEditError("cycle draft not found")
        self.cycle_draft.cancelled_at = cancelled_at
        self.cycle_draft = None
        self.intent_proposal = None
        self.review_gate = None
        self.status = PlanStatus.PAUSED if self.active_cycle is not None else PlanStatus.IDLE

    def open_block(self, block: PlanBlock) -> None:
        """Domain unfreeze #13: a block with a `goal_id`, opened while an
        active cycle exists, is scoped to that goal only — it never freezes
        an unrelated sibling goal's progress. Every other block (no goal_id —
        e.g. `reasoner_failure`/`project_binding`, or any block on a legacy
        non-cyclic plan) keeps the original plan-wide scalar behavior,
        byte-identical to before this unfreeze."""
        if self.active_cycle is not None and block.goal_id is not None:
            current = self.goal_blocks.get(block.goal_id)
            if current is not None and current.active:
                raise InvalidEditError(f"goal '{block.goal_id}' already has an active block")
            self.goal_blocks[block.goal_id] = block
            self._recompute_cyclic_status(block.created_at)
            return
        if self.block is not None and self.block.active:
            raise InvalidEditError("a plan block is already active")
        self.block = block
        self.status = PlanStatus.BLOCKED

    def resolve_block(
        self, resolution: str, resolved_at: datetime, goal_id: str | None = None
    ) -> None:
        """`goal_id=None` resolves the legacy plan-wide scalar block
        (byte-identical to pre-#13 behavior). `goal_id=<x>` resolves only
        that goal's block, leaving any other active goal_blocks (and
        whatever those goals are doing) untouched — the plan's overall
        status is re-derived, not unconditionally forced to PAUSED/IDLE,
        since other goals may still be running."""
        if goal_id is not None:
            block = self.goal_blocks.get(goal_id)
            if block is None or not block.active:
                raise InvalidEditError(f"no active block for goal '{goal_id}'")
            if resolution not in block.legal_resolutions:
                raise InvalidEditError(f"block resolution '{resolution}' is not legal")
            block.resolution = resolution
            block.resolved_at = resolved_at
            self._recompute_cyclic_status(resolved_at)
            return
        if self.block is None or not self.block.active:
            raise InvalidEditError("no active plan block")
        if resolution not in self.block.legal_resolutions:
            raise InvalidEditError(f"block resolution '{resolution}' is not legal")
        self.block.resolution = resolution
        self.block.resolved_at = resolved_at
        self.status = PlanStatus.PAUSED if self.active_cycle is not None else PlanStatus.IDLE

    def open_completion_gate(self, gate: ReviewGate, evidence_refs: list[str]) -> None:
        cycle = self.active_cycle
        if cycle is None:
            raise InvalidEditError("no active cycle to verify")
        cycle.evidence_refs = list(evidence_refs)
        self._open_review_gate(gate)

    def record_output_disposition(
        self,
        gate_id: str,
        revision: int,
        disposition: OutputDisposition,
        output_reference: str | None,
        resolved_at: datetime,
    ) -> None:
        cycle = self.active_cycle
        if cycle is None:
            raise InvalidEditError("no active cycle")
        if disposition != OutputDisposition.DISCARD and not output_reference:
            raise InvalidEditError("successful output disposition requires a reference")
        self._resolve_review_gate(gate_id, revision, disposition.value, resolved_at)
        cycle.output_disposition = disposition
        cycle.output_reference = output_reference
        cycle.completed_at = resolved_at
        if disposition == OutputDisposition.DISCARD:
            cycle.cancelled_at = resolved_at
        cycle.status = (
            CycleStatus.CANCELLED
            if disposition == OutputDisposition.DISCARD
            else CycleStatus.COMPLETED
        )
        self.review_gate = None
        self.status = PlanStatus.IDLE

    # ---- creation-time agent binding ----
    def bind_agents(self, agents: list[AgentSpec], default_agent_id: str) -> list[str]:
        """Bind unbound tasks to agents by capability. Returns task ids that fell
        back to the default (caller emits AgentFellBackToDefault events)."""
        fell_back: list[str] = []
        for goal in self.execution_goals:
            for task in goal.tasks:
                if task.agent_id is None:
                    agent_id, used_default = match_agent(
                        task.required_capabilities, agents, default_agent_id
                    )
                    task.agent_id = agent_id
                    if used_default:
                        fell_back.append(task.id)
        return fell_back
