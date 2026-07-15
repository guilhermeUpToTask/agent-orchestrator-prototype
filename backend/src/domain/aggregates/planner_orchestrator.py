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
    # Long-lived project ownership and cyclic lifecycle.
    project_id: str | None = None
    status: PlanStatus = PlanStatus.WAITING
    cycles: list[Cycle] = []
    intent_proposal: IntentProposal | None = None
    cycle_draft: CycleDraft | None = None
    review_gate: ReviewGate | None = None
    block: PlanBlock | None = None
    pause_requested: bool = False
    # Durable check-to-merge reservation. While set, pause requests may land,
    # but lifecycle/artifact mutations that could supersede the candidate may not.
    promotion_reservation: str | None = None
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
    # the nine-phase enum stays frozen. Armed by a human pause command or by the
    # auto-pause that replaced terminal goal failure; cleared by resume() (which
    # doubles as the manual retry) and by the phase-changing human commands.
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

    def assert_lifecycle_mutation_allowed(self) -> None:
        if self.promotion_reservation is not None:
            raise InvalidEditError("a verified Git promotion is in progress")

    def reserve_promotion(self, reservation: str) -> None:
        if self.promotion_reservation not in (None, reservation):
            raise InvalidEditError("another verified Git promotion is in progress")
        self.promotion_reservation = reservation

    def release_promotion(self, reservation: str) -> None:
        if self.promotion_reservation == reservation:
            self.promotion_reservation = None

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

    # ---- graceful human pause gate ----
    def request_pause(self, active_action: bool, reason: str | None = None) -> None:
        """Block new claims immediately; settle only after an active action finalizes."""
        if self.paused or self.pause_requested:
            if reason is not None:
                self.paused_reason = reason
            return
        if self.status != PlanStatus.RUNNING or self.phase not in WORKER_CLAIMABLE_PHASES:
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
        if self.phase not in WORKER_CLAIMABLE_PHASES:
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

    def retry_task(self, goal_id: str, task_id: str, resolved_at: datetime) -> None:
        """Retry exactly one failed task; absolute attempt identity is preserved."""
        blocked = self.block is not None and self.block.active
        if not self.paused and not blocked:
            raise InvalidTransitionError("Plan", self.id, self.status.value, "retry")
        if blocked and (
            self.block is None
            or self.block.goal_id != goal_id
            or self.block.task_id != task_id
            or "retry_stage" not in self.block.legal_resolutions
        ):
            raise InvalidEditError("retry does not target the active plan block")
        goal = self._goal(goal_id)
        self._task(goal, task_id).retry()
        if blocked:
            assert self.block is not None
            self.block.resolution = "retry_stage"
            self.block.resolved_at = resolved_at
            self.status = PlanStatus.RUNNING

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
        self._set_phase(PlanPhase.REPLANNING)

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
    def status_reason(self) -> dict[str, str | None]:
        if self.promotion_reservation is not None:
            return {
                "kind": "promotion",
                "code": "git_promotion",
                "message": "Promoting verified code at an atomic boundary.",
            }
        if self.block is not None and self.block.active:
            return {
                "kind": "block",
                "code": self.block.kind,
                "message": self.block.explanation,
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
        if self.promotion_reservation is not None:
            return ["pause"] if self.status == PlanStatus.RUNNING else []
        if self.block is not None and self.block.active:
            return list(self.block.legal_resolutions)
        if self.review_gate is not None and self.review_gate.unresolved:
            return [f"review:{decision}" for decision in self.review_gate.allowed_decisions]
        if self.pause_requested:
            return []
        if self.status == PlanStatus.RUNNING:
            return ["pause", "start_replan"]
        if self.status == PlanStatus.PAUSED:
            return [
                "resume",
                "start_replan",
                "edit_pending_work",
                "cancel_cycle",
            ]
        if self.status == PlanStatus.IDLE or (
            self.status == PlanStatus.WAITING
            and self.review_gate is None
            and self.intent_proposal is None
        ):
            return ["start_intent"]
        return []

    @property
    def activity(self) -> str:
        """Derived activity; never persisted as a second lifecycle enum."""
        if self.block is not None and self.block.active:
            return f"blocked:{self.block.stage}"
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
        if self.block is not None and self.block.active:
            raise InvalidEditError("a plan block is already active")
        self.block = block
        self.status = PlanStatus.BLOCKED

    def resolve_block(self, resolution: str, resolved_at: datetime) -> None:
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
