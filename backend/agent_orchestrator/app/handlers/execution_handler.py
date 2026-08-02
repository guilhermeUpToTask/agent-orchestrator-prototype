"""ExecutionHandler — owns the RUNNING phase: the pull-scan task loop.

This is the crash-safety choreography (two-transaction write, check-before-act
idempotency, durable backoff gate, transactional outbox, retry-vs-terminal). It was
the core of the old advance_plan; extracted here so task execution is one isolated
concern that adding planning phases can never disturb.

In the cyclic ProjectPlan, exhausted or permanent task failures open a structured
block whose advertised actions are explicit retry, edit, or replan commands. Resume
releases only a manual pause. Late results landing after a mid-RUNNING replan are handled by the TOLERANT FINALIZE: the finalize transactions re-check
plan.phase — a late failure terminal-skips (never requeues into an abandoned
iteration), a late success completes as harmless history unless the task was
already closed by the finalize-abandon.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Mapping, Sequence, TypeVar
from uuid import uuid4

from agent_orchestrator.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from agent_orchestrator.domain.errors.tasks_errors import StaleVersionError
from agent_orchestrator.domain.entities.agent_spec import AgentSpec
from agent_orchestrator.domain.entities.execution_contracts import (
    TaskContract,
    TestBundle,
    VerificationEvidence,
    VerificationKind,
    VerificationStrategy,
)
from agent_orchestrator.domain.entities.goal import Goal
from agent_orchestrator.domain.entities.planning_artifacts import (
    PlanBlock,
    PlanStatus,
    ReviewGate,
    ReviewSubjectType,
)
from agent_orchestrator.domain.entities.task import Task
from agent_orchestrator.domain.events.outbox import (
    CycleVerified,
    GoalCompleted,
    PhaseAdvanced,
    PlanBlocked,
    PlanPaused,
    TaskAbandoned,
    TaskCompleted,
    TaskFailedEvent,
    TaskRequeued,
    TaskStarted,
    TestBundleFrozen,
    TaskVerificationAccepted,
)
from agent_orchestrator.domain.factories.identity import new_id
from agent_orchestrator.domain.policies.retry_policies import RetryPolicy
from agent_orchestrator.domain.errors.base import DomainError
from agent_orchestrator.domain.repositories.agent_repo import AgentRepository
from agent_orchestrator.domain.repositories.model_provider_repo import ModelProviderRepository
from agent_orchestrator.domain.services.lookups import find_goal, find_task
from agent_orchestrator.domain.services.navigation import NOT_READY, can_promote_goal
from agent_orchestrator.domain.value_objects.lifecycle import Status
from agent_orchestrator.domain.value_objects.lifecycle import FailureKind
from agent_orchestrator.domain.value_objects.tasks_vos import TaskResult

from agent_orchestrator.app.block_policy import resolutions_for
from agent_orchestrator.app.acceptance_records import AcceptanceRun
from agent_orchestrator.app.branch_names import cycle_branch, goal_branch
from agent_orchestrator.app.environment_port import (
    AcceptanceTrigger,
    EnvironmentSpec,
    ProjectEnvironment,
)
from agent_orchestrator.app.promotion_records import GoalPromotion
from agent_orchestrator.app.execution_records import (
    ExecutionAttempt,
    ExecutionAttemptStatus,
    ExecutionRun,
    ExecutionRunStatus,
    RuntimeCircuit,
)
from agent_orchestrator.app.ports import PlanningArtifact, PlanningArtifactStore, RepositoryReader
from agent_orchestrator.app.provider_capacity import (
    CAPACITY_KINDS,
    CapacityScope,
    ProviderCapacityPolicy,
    RoutingPolicy,
    capacity_backoff_seconds,
    circuit_model_id,
    circuit_ref,
    resolve_capacity_scope,
    resolve_max_inflight,
)
from agent_orchestrator.app.runtime_failures import LimitScope, RuntimeFailure, safe_runtime_tail
from agent_orchestrator.app.handlers.base import Signal
from agent_orchestrator.app.ports import (
    AgentEventSink,
    AgentRunner,
    Clock,
    CommandExecution,
    MainRepositoryWorkspace,
    TaskFailed,
    UnitOfWork,
    Workspace,
    VerificationExecutor,
    WorkspaceHandle,
)
import structlog

from agent_orchestrator.app.agent_feedback import split_reasons
from agent_orchestrator.app.contract_repair import propose_repair
from agent_orchestrator.app.promotion_failures import is_transient_merge_failure
from agent_orchestrator.domain.services.edit_service import resync_goal_contract
from agent_orchestrator.app.test_identity import (
    DeclaredChecks,
    criterion_test_map,
    declared_checks,
    existing_checks,
)
from agent_orchestrator.app.verification import (
    BaselineOutcome,
    baseline_outcome,
    is_check_path,
    sha256_file,
    validate_authoring,
    test_author_path_allowed,
    validate_candidate,
)

_T = TypeVar("_T")

# Distinct contract repairs attempted before a human is asked. Bounded by
# ATTEMPTS, not wall clock: unlike a provider outage, time does not heal a wrong
# contract, so a clock-based bound would only spin.
MAX_CONTRACT_REPAIRS = 2

# Re-attempts of a goal->cycle merge that failed for an ENVIRONMENTAL reason. The
# merge is cheap; if two re-attempts do not clear it, the condition is not
# momentary and a human should see it.
MAX_PROMOTION_RETRIES = 2

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class _Unit:
    """Revision-bound values captured in the start transaction."""

    cycle_id: str | None
    goal_id: str
    task_id: str
    attempt: int
    policy_attempt: int
    task_revision: int
    retry_policy: RetryPolicy
    task_snapshot: Task
    spec: AgentSpec
    execution: ExecutionAttempt
    run_role: str


def _orchestration_failure(reason: str) -> RuntimeFailure:
    """A Class C failure: an orchestration race or missing infrastructure.

    Un-freeze #17 made VERIFICATION_ERROR retryable so a rejected CANDIDATE gets
    a second attempt. These are not candidates — a superseded cycle, evidence
    that moved during promotion, an absent verifier — and retrying only re-races
    them. `RuntimeFailure.retryable` is an independent veto in the retry
    condition, so the split stays structural rather than keyed off message text.
    """
    return RuntimeFailure(
        kind=FailureKind.VERIFICATION_ERROR,
        safe_message=reason[:500],
        retryable=False,
    )



def _run_role_for(plan: Plan, task: Task) -> str:
    """Which stage runs next for this task: author the checks, or implement.

    One definition, because `_start_unit` and `_resolve_spec` must agree — they
    each had their own copy of this expression, and a divergence would resolve an
    agent for one role while running the other.
    """
    if plan.active_cycle is None or task.contract is None:
        return "implementer"
    if task.test_bundle is not None and task.test_bundle.validates(task.id, task.revision):
        return "implementer"
    return "test_author"


class ExecutionHandler:
    def __init__(
        self,
        runner: AgentRunner,
        agents: AgentRepository,
        workspace: Workspace,
        event_sink: AgentEventSink,
        clock: Clock,
        verifier: VerificationExecutor | None = None,
        capacity: ProviderCapacityPolicy | None = None,
        providers: ModelProviderRepository | None = None,
        routing: RoutingPolicy | None = None,
        repository_reader: RepositoryReader | None = None,
        planning_artifacts: PlanningArtifactStore | None = None,
        environment: ProjectEnvironment | None = None,
        # (repository path, how to boot it) for one plan. A callable rather than
        # a Workspace method: `Workspace` is a FROZEN domain port, and resolving
        # a project's checkout is a composition-root job the container already
        # does for readiness and publication.
        environment_context: Callable[[str], tuple[Path, EnvironmentSpec | None]] | None = None,
    ) -> None:
        self._runner = runner
        self._agents = agents
        self._workspace = workspace
        self._event_sink = event_sink
        self._clock = clock
        self._verifier = verifier
        self._capacity = capacity or ProviderCapacityPolicy()
        # Optional: without it, capacity metadata falls back to the global policy.
        # Dry-run and unit tests need no provider catalog.
        self._providers = providers
        self._routing = routing or RoutingPolicy()
        # Contract repair needs repository facts to derive a patch from, and a
        # durable place to count its own attempts. Without either it simply never
        # fires and a block opens exactly as before.
        self._repository_reader = repository_reader
        self._planning_artifacts = planning_artifacts
        # Artifact writes queued while a plan transaction is OPEN, flushed once
        # it closes. This store deliberately writes on its own short transaction
        # so the record survives a plan rollback — which means a second SQLite
        # connection, and SQLite/WAL allows exactly one writer. Appending from
        # inside `with uow:` therefore self-deadlocks: the plan connection holds
        # the write lock and blocks waiting for the artifact connection, which is
        # waiting for the plan connection. `busy_timeout` and the `run_in_session`
        # retry budget cannot break it, because the caller IS the holder.
        self._pending_artifacts: list[PlanningArtifact] = []
        # The cycle acceptance run (P8.2). Optional: without an adapter the
        # orchestrator behaves exactly as it did, which is why NoEnvironment is
        # the permanent fallback rather than a placeholder. The verdict is
        # ADVISORY — it is recorded beside the cycle and gates nothing.
        self._environment = environment
        self._environment_context = environment_context

    async def handle_goal(self, plan_id: str, goal_id: str, plan: Plan, uow: UnitOfWork) -> Signal:
        """Goal-level parallelism (ADR-001, domain unfreeze #13): the caller is
        a goal-scoped worker that already holds `goal_id`'s lease. Drives that
        ONE goal via the identical body `handle()` uses for the plan-level
        (legacy/pre-goal-parallelism) path — see `handle`'s `goal_id` param."""
        return await self.handle(plan_id, plan, uow, goal_id=goal_id)

    async def handle(
        self, plan_id: str, plan: Plan, uow: UnitOfWork, goal_id: str | None = None
    ) -> Signal:
        # ---- txn1: pick the unit, mark RUNNING, persist + outbox atomically ----
        goal_promotion: tuple[str, str, str] | None = None
        unit: _Unit | None = None
        review_signal: Signal | None = None
        completion_cycle_id: str | None = None
        with uow:
            plan = uow.plans.get(plan_id)
            if (
                plan.paused
                or plan.pause_requested
                or plan.status == PlanStatus.BLOCKED
                or (plan.block is not None and plan.block.active)
            ):
                return Signal.PAUSED  # pause gate armed while we were dispatched
            now = self._clock.now()
            action = (
                plan.peek_next_for_goal(goal_id, now)
                if goal_id is not None
                else plan.peek_next(now)
            )

            if action is None:
                if goal_id is not None:
                    # Domain unfreeze #14: None here means "THIS goal is
                    # already terminal" (peek_next_for_goal's own contract),
                    # NOT "the whole plan is done" -- entering review is a
                    # plan-wide transition that must never fire just because
                    # one goal-lease worker's goal happens to be finished
                    # while siblings are still running. The plan-level tick
                    # (advance_plan.py) is what checks "every goal terminal"
                    # and calls handle(goal_id=None) for that.
                    return Signal.PAUSED
                review_signal = self._enter_review(plan_id, plan, uow)
                # A cyclic completion gate just opened. The acceptance run
                # boots an application, which is a side effect and must not
                # happen inside this transaction (invariant #5) — so the
                # cycle is remembered and verified just past the block.
                completion_cycle_id = (
                    plan.active_cycle.id if plan.active_cycle is not None else None
                )
            elif action == NOT_READY:
                return Signal.NOT_READY
            else:
                goal, second = action
                if second is None:  # goal's tasks all terminal, none failed -> close it
                    if plan.active_cycle is None:
                        return self._complete_legacy_goal(plan_id, plan, goal, uow)
                    try:
                        goal_promotion = self._reserve_goal_promotion(plan, goal, uow)
                    except TaskFailed as failure:
                        # Navigation selected this goal to close, but a task is not
                        # DONE-with-accepted-evidence (e.g. a legacy/replan artifact).
                        # Open a recoverable block — never let this escape to the
                        # worker loop, which re-runs the reservation and hot-loops the
                        # same TaskFailed every tick.
                        return self._block_on_unpromotable_goal(plan_id, plan, goal, failure, uow)
                else:
                    if second == "GOAL_FAILED":
                        return self._pause_on_failed_goal(plan_id, plan, goal, uow)
                    if second == "DEPENDENCY_BLOCKED":
                        return Signal.NOT_READY

                    task = second
                    # check-before-act: result already exists -> finalize without re-running.
                    if task.result is not None:
                        return self._finalize_existing(plan_id, plan, goal, task, uow)

                    spec = self._select_spec(plan, task, uow)
                    # Admission BEFORE the circuit check: if the pool is already full
                    # there is nothing to probe, and claiming the half-open probe only
                    # to be refused would burn the window for everyone.
                    admission_signal = self._admission_signal(spec, uow)
                    if admission_signal is not None:
                        return admission_signal
                    circuit_signal = self._runtime_circuit_signal(plan_id, plan, goal, task, spec, uow)
                    if circuit_signal is not None:
                        return circuit_signal
                    unit = self._start_unit(plan_id, plan, goal, task, uow, spec)

        if review_signal is not None:
            # The publication gate is already open; this only adds the advisory
            # verdict an operator will read before deciding. Gating the gate on
            # it would make a flaky acceptance run able to withhold publication,
            # which the design explicitly rejects.
            if completion_cycle_id is not None:
                self._run_acceptance(
                    plan_id, completion_cycle_id, None, "pre_publication", uow
                )
            return review_signal

        if goal_promotion is not None:
            return await self._promote_goal(plan_id, goal_promotion, uow)
        assert unit is not None

        # ---- side effect OUTSIDE any transaction. Runner just executes — no policy. ----
        key = (
            f"{plan_id}:{unit.goal_id}:{unit.task_id}:{unit.execution.run_id}:"
            f"{unit.execution.number}:{unit.execution.id}"
        )
        handle = await self._workspace.begin(
            plan_id,
            unit.task_id,
            unit.execution.number,
            cycle_id=unit.cycle_id,
            goal_id=unit.goal_id,
            run_id=unit.execution.run_id,
            base_ref=(
                unit.task_snapshot.test_bundle.test_commit_sha
                if unit.run_role == "implementer" and unit.task_snapshot.test_bundle is not None
                else None
            ),
        )
        main_repo_before: set[str] = set()
        # Taken BEFORE the agent runs, because afterwards there is no way to tell
        # a check the author added from one that was already in the tree. Anything
        # new in the diff is then unambiguously this task's, and everything in
        # here stays protected against both stages.
        checks_before: dict[str, str] = (
            existing_checks(Path(handle.path)) if unit.run_role == "test_author" else {}
        )
        # No agent when the contract NAMES a check that is already here. The
        # authoring prompt says "write tests that fail"; handing that to an agent
        # with nothing to write means relying on it to disobey, and the correct
        # output — an empty diff — is indistinguishable from the agent giving up.
        # Freezing from the declaration instead is deterministic and free.
        #
        # The decision needs the worktree (existence is a fact about the tree), so
        # it lands here rather than beside `_start_unit`. Consequence: the
        # admission and circuit gates upstream still applied, which can delay a
        # stage needing no provider — conservative, and it never lets one proceed
        # that should have waited.
        # Resolved ONCE, here, before the agent could change the tree. That fixes
        # what "declared" means: a check the contract named that was ALREADY
        # present when the stage began. Resolving it again after the agent ran
        # would quietly also match a file the agent just created.
        declared = (
            declared_checks(unit.task_snapshot.contract, Path(handle.path))
            if unit.run_role == "test_author" and unit.task_snapshot.contract is not None
            else DeclaredChecks()
        )
        try:
            main_repo_before = await self._main_repo_status()
            result: TaskResult = TaskResult.success("orchestrator froze a declared check")
            if not declared.declared:
                result = await self._runner.run(
                    unit.task_snapshot,
                    unit.spec,
                    idempotency_key=key,
                    event_sink=self._event_sink,
                    workspace=handle,
                )
                await self._raise_on_main_repo_changes(main_repo_before)
            if unit.cycle_id is not None and unit.task_snapshot.contract is not None:
                if self._verifier is None:
                    raise TaskFailed(
                        "deterministic verification executor is unavailable",
                        # Class C (a missing verifier is configuration, not a candidate the agent can fix). Un-freeze #17 made
                        # VERIFICATION_ERROR retryable for candidate rejections;
                        # this is not one, so it keeps the `retryable` veto.
                        failure=_orchestration_failure("deterministic verification executor is unavailable"),
                    )
                if unit.run_role == "test_author":
                    return await self._finalize_test_author(
                        plan_id, unit, handle, uow, checks_before, declared
                    )
                return await self._finalize_verified_implementation(plan_id, unit, handle, uow)
            if not self._reserve_candidate(plan_id, unit, uow):
                await self._workspace.discard(handle)
                self._abandon_stale(plan_id, unit, uow)
                return Signal.PAUSED
            await self._workspace.commit(handle)
        except TaskFailed as failure:
            exc = failure
            stray_paths = await self._main_repo_stray_paths(main_repo_before)
            if stray_paths:
                exc = self._main_repo_failure(stray_paths)
            await self._workspace.discard(handle)
            return self._finalize_failure(plan_id, unit, exc, uow)

        return self._finalize_success(plan_id, unit, result, uow)

    async def _main_repo_status(self) -> set[str]:
        if isinstance(self._workspace, MainRepositoryWorkspace):
            return await self._workspace.main_repo_status()
        return set()

    @staticmethod
    def _main_repo_failure(stray_paths: list[str]) -> TaskFailed:
        message = (
            "agent modified the project main repository outside its assigned "
            f"worktree; stray paths: {stray_paths}"
        )
        return TaskFailed(
            message,
            FailureKind.TOOL_ERROR,
            failure=RuntimeFailure(
                kind=FailureKind.TOOL_ERROR,
                safe_message=message,
                retryable=False,
            ),
        )

    async def _main_repo_stray_paths(self, before: set[str]) -> list[str]:
        if not isinstance(self._workspace, MainRepositoryWorkspace):
            return []
        after = await self._workspace.main_repo_status()
        return sorted(line[3:] for line in after - before)

    async def _raise_on_main_repo_changes(self, before: set[str]) -> None:
        stray_paths = await self._main_repo_stray_paths(before)
        if stray_paths:
            raise self._main_repo_failure(stray_paths)

    def _retry_promotion(
        self,
        plan_id: str,
        goal_id: str,
        reservation: str,
        exc: Exception,
        uow: UnitOfWork,
    ) -> bool:
        """Release the reservation and let a later tick re-attempt the merge.

        Only for environmental failures, and only a bounded number of times. The
        reservation MUST be released either way — holding it would block the very
        retry this exists to allow, and `assert_lifecycle_mutation_allowed`
        refuses every edit while one is open.

        Bounded by attempts rather than wall clock: the merge is cheap, so if two
        re-attempts do not clear it the condition is not momentary, and a human
        should see it.
        """
        if self._planning_artifacts is None:
            return False  # nowhere to count, so no unbounded loop is possible
        if not is_transient_merge_failure(str(exc)):
            return False

        prior = self._planning_artifacts.latest(
            plan_id, "goal_promotion_retry", goal_id=goal_id, limit=MAX_PROMOTION_RETRIES + 1
        )
        if len(prior) >= MAX_PROMOTION_RETRIES:
            return False

        with uow:
            plan = uow.plans.get(plan_id)
            if plan.goal_promotion_reservations.get(goal_id) != reservation:
                return False  # someone else owns it now; do not touch their state
            plan.release_promotion(goal_id, reservation)
            plan.bump_version()
            uow.plans.save(plan)

        self._planning_artifacts.append(
            PlanningArtifact(
                plan_id=plan_id,
                goal_id=goal_id,
                purpose="goal_promotion_retry",
                sequence=0,
                input_fingerprint=goal_id,
                outcome="abandoned",
                rejection_reasons=(str(exc)[:500],),
                created_at=self._clock.now(),
            )
        )
        log.info("promotion.retrying", plan_id=plan_id, goal_id=goal_id, error=str(exc)[:200])
        return True

    def _flush_pending_artifacts(self) -> None:
        """Write everything queued during a now-closed plan transaction.

        Best-effort by the store's own contract: losing the record must never
        fail a run. Draining before the write means a store that raises cannot
        make the next flush replay the same rows.
        """
        pending, self._pending_artifacts = self._pending_artifacts, []
        if self._planning_artifacts is None:
            return
        for artifact in pending:
            try:
                self._planning_artifacts.append(artifact)
            except Exception as error:  # noqa: BLE001 — never fail a run for a record
                log.warning(
                    "planning_artifact.write_failed",
                    plan_id=artifact.plan_id,
                    purpose=artifact.purpose,
                    error=str(error),
                )

    def _repair_contract(
        self,
        plan_id: str,
        plan: Plan,
        unit: "_Unit",
        exc: TaskFailed,
        uow: UnitOfWork,
        not_before: datetime | None,
    ) -> bool:
        """Fix an unsatisfiable contract in place and requeue, or return False.

        Bounded by DISTINCT repairs attempted, not by wall clock: unlike a
        provider outage, time does not heal a wrong contract, so a clock-based
        bound would just spin. Past the bound the block opens exactly as before —
        by then a repair and the full retry budget have both failed, which is
        genuinely a human's problem.

        Every repair is recorded (visible at GET /plans/{id}/planning-artifacts)
        and requeues the task with a `TaskRequeued` naming what changed, so an
        automatic loop is observable rather than a silent spinner.
        """
        if self._repository_reader is None or self._planning_artifacts is None:
            return False
        if plan.project_id is None:
            return False
        try:
            goal = plan._goal(unit.goal_id)
            task = plan._task(goal, unit.task_id)
        except Exception:  # noqa: BLE001 — a vanished task is not repairable
            return False
        if task.contract is None:
            return False

        prior = self._planning_artifacts.latest(
            plan_id, "contract_repair", goal_id=unit.goal_id, limit=MAX_CONTRACT_REPAIRS + 1
        )
        attempted = sum(
            1
            for item in prior
            if item.payload is not None and item.payload.get("task_id") == task.id
        )
        if attempted >= MAX_CONTRACT_REPAIRS:
            return False

        try:
            tracked = self._repository_reader.list_paths(plan.project_id, max_entries=2000)
        except Exception as read_error:  # noqa: BLE001 — no sight, no repair
            log.warning("contract_repair.repository_unavailable", error=str(read_error))
            return False

        repair = propose_repair(task.contract, split_reasons(exc.reason), tracked)
        if repair is None:
            return False

        try:
            # Requeue FIRST: the task is still RUNNING at this point and
            # `amend_contract` guards {PENDING, FAILED} — an in-flight task must
            # not have its contract rewritten underneath it.
            plan.requeue_task(unit.goal_id, unit.task_id, not_before)
            task.amend_contract(
                allowed_scope=repair.allowed_scope,
                verification_commands=repair.verification_commands,
            )
            resync_goal_contract(goal)
        except Exception as amend_error:  # noqa: BLE001 — never worsen a failure
            log.warning("contract_repair.rejected", task_id=task.id, error=str(amend_error))
            return False

        # QUEUED, not written: this runs inside the finalize transaction, and
        # writing here deadlocks the two connections against each other. Flushed
        # by `_finalize_failure` once the transaction closes.
        self._pending_artifacts.append(
            PlanningArtifact(
                plan_id=plan_id,
                goal_id=unit.goal_id,
                purpose="contract_repair",
                sequence=0,
                input_fingerprint=f"{task.id}:{task.revision}",
                outcome="committed",
                payload={"task_id": task.id, "repair": repair.description},
                rejection_reasons=(exc.reason,),
                created_at=self._clock.now(),
            )
        )
        self._finish_execution(
            uow,
            unit,
            ExecutionAttemptStatus.FAILED,
            ExecutionRunStatus.RETRYING,
            failure=exc.failure,
            retry_at=not_before,
        )
        plan.bump_version()
        uow.outbox.add(
            TaskRequeued(
                plan_id=plan_id,
                goal_id=unit.goal_id,
                task_id=unit.task_id,
                attempt=unit.attempt,
                reason=f"contract repaired automatically: {repair.description}",
                kind=exc.kind.value if exc.kind else None,
            )
        )
        uow.plans.save(plan)
        log.info(
            "contract_repair.applied",
            plan_id=plan_id,
            task_id=task.id,
            repair=repair.description,
        )
        return True

    def _attempts_against_budget(
        self, plan_id: str, unit: "_Unit", kind: FailureKind | None, uow: UnitOfWork
    ) -> int:
        """How many attempts this failure's budget has actually consumed.

        `cycle_attempt` counts EVERY attempt, including ones that never reached
        the work: a provider capacity failure means there was no room upstream,
        not that the agent produced something wrong. For a kind bounded by a
        ceiling that distinction decides the outcome — observed live, a task
        waited out three rate limits and then its FIRST real candidate rejection
        blocked the goal, because the ceiling of 2 had already been spent by
        failures of an unrelated kind.

        Kinds with a ceiling are therefore counted per KIND. Everything else
        keeps the whole-task counter, which is what a general attempt budget
        means.
        """
        if kind is None or kind not in unit.retry_policy.kind_attempt_ceiling:
            return unit.policy_attempt
        # The ledger holds only attempts already finalized; this one is still
        # RUNNING (finalize happens after the retry decision). Count it, so the
        # number means the same thing `policy_attempt` does: attempts made,
        # including the one that just failed.
        prior = sum(
            1
            for attempt in uow.executions.list_attempts(plan_id)
            if attempt.task_id == unit.task_id
            and attempt.goal_id == unit.goal_id
            and attempt.failure_kind == kind.value
        )
        return prior + 1

    @staticmethod
    def _raise_on_infrastructure_exit(outcomes: list[CommandExecution]) -> None:
        """Exit 126/127 means the command could not run at all — never a test verdict."""
        failure = next((item for item in outcomes if item.exit_code in {126, 127}), None)
        if failure is not None:
            raise TaskFailed(
                f"verification command {failure.command!r} failed with exit code "
                f"{failure.exit_code} (infrastructure failure)",
                FailureKind.TOOL_ERROR,
            )

    @staticmethod
    def _test_author_path_allowed(path: str, strategy: VerificationStrategy) -> bool:
        # One definition, shared with the reasoner's submission-time check so a
        # contract cannot freeze a strategy its own scope makes unsatisfiable.
        return test_author_path_allowed(path, strategy)



    def _record_baseline(
        self,
        plan_id: str,
        unit: _Unit,
        contract: TaskContract,
        selectors: Sequence[str],
        outcomes: Sequence[CommandExecution],
        baseline: BaselineOutcome,
    ) -> None:
        """Whether the checks were red or green BEFORE the work.

        That single fact decides whether the green afterwards means anything, and
        it was stored nowhere queryable: `TestBundle.baseline_evidence_refs` is
        always empty, and `red_or_baseline_evidence_refs` holds only sha256
        digests of output text — you can prove a command ran, not what it decided.

        A `PlanningArtifact` rather than a field on the bundle: the bundle rides
        the plan's version CAS and would roll back with the transaction, while
        these writes run on their own short transaction (the same reason
        `ChatStore` does). Best-effort — losing the record must never fail a run
        that succeeded. Served by `GET /plans/{id}/planning-artifacts`.
        """
        if self._planning_artifacts is None:
            return
        try:
            self._planning_artifacts.append(
                PlanningArtifact(
                    plan_id=plan_id,
                    goal_id=unit.goal_id,
                    purpose="verification_baseline",
                    operation_id=unit.execution.id,
                    sequence=0,  # the store assigns the next one
                    input_fingerprint=f"{unit.task_id}:{unit.task_revision}",
                    outcome="committed",
                    created_at=self._clock.now(),
                    payload={
                        "task_id": unit.task_id,
                        "task_revision": unit.task_revision,
                        "strategy": contract.verification_strategy.value,
                        "checks": list(selectors),
                        "commands": [item.command for item in outcomes],
                        "exit_codes": [item.exit_code for item in outcomes],
                        "verdict": baseline.verdict,
                    },
                )
            )
        except Exception:  # pragma: no cover - telemetry must never fail a run
            log.warning("execution.baseline_record_failed", plan_id=plan_id, task_id=unit.task_id)

    def _reject_authoring_violations(
        self,
        root: Path,
        checks_before: Mapping[str, str],
        changed: Sequence[str],
        *,
        violated: str,
        intruded: str,
    ) -> None:
        """The authoring stage's rules, applied to whatever `actor` just touched.

        The messages are passed in as whole literals rather than composed from an
        actor noun, because `test_agent_feedback` greps `src` to prove every
        whitelisted rejection prefix is still emitted somewhere — an f-string
        would make that guard silently vacuous.

        Called twice: once for the agent's diff, once for the tree AFTER its own
        verification command ran. The second call is not paranoia — the command is
        arbitrary shell from the contract, and until it shared this guard it was
        judged by `is_check_path` alone, so a command could rewrite another task's
        check during the baseline and freeze the result as this task's evidence.
        """
        verdict = validate_authoring(root, checks_before, changed)
        if not verdict.accepted:
            raise TaskFailed(
                f"{violated}: " + "; ".join(verdict.reasons),
                FailureKind.VERIFICATION_ERROR,
            )
        intruders = [
            path for path in changed if path not in checks_before and not is_check_path(path)
        ]
        if intruders:
            raise TaskFailed(
                f"{intruded}: {intruders}",
                FailureKind.VERIFICATION_ERROR,
            )

    async def _finalize_test_author(
        self,
        plan_id: str,
        unit: _Unit,
        handle: WorkspaceHandle,
        uow: UnitOfWork,
        checks_before: Mapping[str, str],
        declared: DeclaredChecks,
    ) -> Signal:
        assert self._verifier is not None
        contract = unit.task_snapshot.contract
        assert contract is not None
        workspace_handle = handle
        base_ref = getattr(workspace_handle, "base_ref", None)
        # THREE names, never one reused. `checks` is what gets hashed into the
        # bundle, and an earlier revision of this function recomputed `paths`
        # after running the commands — silently replacing the protected set with
        # whatever the commands happened to leave behind, which for an empty diff
        # is nothing at all. A bundle protecting `{}` lets the implementer rewrite
        # the very tests it must satisfy.
        authored = await self._verifier.changed_paths(workspace_handle.path, base_ref)

        self._reject_authoring_violations(
            Path(workspace_handle.path),
            checks_before,
            authored,
            violated="test author violated the frozen checks",
            intruded="test author modified production paths",
        )

        # New = this task's, by construction. A path that was already in the tree
        # belongs to whoever put it there, so it is never claimed here.
        new_checks = [path for path in authored if path not in checks_before]
        if new_checks:
            checks, selectors = list(new_checks), list(new_checks)
        elif declared.declared:
            # Certain, or author. An empty diff is correct when the contract NAMES
            # a check that already exists — the ordinary bug-fix workflow. The
            # mapping comes from the contract, never from scanning the tree, so a
            # multi-task goal cannot hand this task another task's failing test.
            checks, selectors = list(declared.files), list(declared.node_ids)
        else:
            raise TaskFailed(
                "test author produced no executable checks",
                FailureKind.VERIFICATION_ERROR,
            )
        outcomes = await self._verifier.run(
            workspace_handle.path,
            contract.verification_commands,
        )
        # Infrastructure first, matching the implementation stage: a command that
        # exits 127 AND left a file behind is an infrastructure failure, not a
        # contract violation, and reporting it as the latter sends the agent to
        # repair something that never ran.
        self._raise_on_infrastructure_exit(outcomes)
        after = await self._verifier.changed_paths(workspace_handle.path, base_ref)
        self._reject_authoring_violations(
            Path(workspace_handle.path),
            checks_before,
            after,
            violated="verification command violated the frozen checks",
            intruded="verification command modified production paths",
        )
        baseline = baseline_outcome(
            contract.verification_strategy, [item.exit_code for item in outcomes]
        )
        self._record_baseline(plan_id, unit, contract, selectors, outcomes, baseline)
        if not baseline.accepted:
            raise TaskFailed(
                f"test bundle did not establish {baseline.expectation}",
                FailureKind.VERIFICATION_ERROR,
            )
        missing = [path for path in checks if not (Path(workspace_handle.path) / path).is_file()]
        if missing:
            raise TaskFailed(
                f"test author deleted or renamed executable checks: {missing}",
                FailureKind.VERIFICATION_ERROR,
            )
        protected = {
            **dict(checks_before),
            **{path: sha256_file(Path(workspace_handle.path) / path) for path in checks},
        }
        if not self._reserve_candidate(plan_id, unit, uow):
            await self._workspace.discard(workspace_handle)
            self._abandon_stale(plan_id, unit, uow)
            return Signal.PAUSED
        test_commit_sha = await self._workspace.checkpoint(workspace_handle)
        evidence_refs = [item.bounded_output_ref for item in outcomes]
        bundle = TestBundle(
            task_id=unit.task_id,
            task_revision=unit.task_revision,
            test_commit_sha=test_commit_sha,
            protected_file_hashes=protected,
            # This task's checks, not "whatever the diff contained". `checks` is
            # the authoring diff (unambiguously this task's) or the contract's
            # own declaration — never a repository scan, which cannot tell one
            # task's tests from another's.
            criterion_to_tests=criterion_test_map(contract, selectors),
            verification_strategy=contract.verification_strategy,
            baseline_evidence_refs=[],
            red_or_baseline_evidence_refs=evidence_refs,
            frozen_at=self._clock.now(),
        )

        def finalize() -> Signal:
            with uow:
                plan = uow.plans.get(plan_id)
                task = self._unit_task(plan, unit)
                if (
                    plan.goal_promotion_reservations.get(unit.goal_id) != unit.execution.id
                    or task.status != Status.RUNNING
                    or task.revision != unit.task_revision
                    or task.attempt != unit.attempt
                ):
                    self._finish_execution(
                        uow,
                        unit,
                        ExecutionAttemptStatus.ABANDONED,
                        ExecutionRunStatus.ABANDONED,
                    )
                    if plan.goal_promotion_reservations.get(unit.goal_id) == unit.execution.id:
                        plan.release_promotion(unit.goal_id, unit.execution.id)
                        plan.bump_version()
                        uow.plans.save(plan)
                    return Signal.PAUSED
                plan.release_promotion(unit.goal_id, unit.execution.id)
                # A stage that made no provider call proves nothing about the
                # provider, so it must not retire an open circuit. Nothing else
                # here is provider-dependent.
                if not declared.declared:
                    self._clear_runtime_circuit(uow, unit)
                task.freeze_test_bundle(bundle)
                plan.requeue_task(unit.goal_id, unit.task_id)
                self._finish_execution(
                    uow,
                    unit,
                    ExecutionAttemptStatus.SUCCEEDED,
                    ExecutionRunStatus.SUCCEEDED,
                )
                paused_at_boundary = self._settle_requested_pause(plan_id, plan, uow)
                plan.bump_version()
                uow.outbox.add(
                    TestBundleFrozen(
                        plan_id=plan_id,
                        goal_id=unit.goal_id,
                        task_id=unit.task_id,
                        task_revision=unit.task_revision,
                        test_commit_sha=test_commit_sha,
                    )
                )
                uow.plans.save(plan)
                return Signal.PAUSED if paused_at_boundary else Signal.CONTINUE

        # StaleVersionError here means a concurrent GOAL's write landed in
        # this block's own get()-to-save() window (domain unfreeze #13); the
        # `with uow:` block above rolls back atomically on any exception
        # (SqliteUnitOfWork.__exit__), so retrying from scratch — including
        # re-running _finish_execution — is safe, not a double-write.
        return self._run_with_cas_retry(finalize)

    async def _finalize_verified_implementation(
        self,
        plan_id: str,
        unit: _Unit,
        handle: WorkspaceHandle,
        uow: UnitOfWork,
    ) -> Signal:
        assert self._verifier is not None
        contract = unit.task_snapshot.contract
        bundle = unit.task_snapshot.test_bundle
        assert contract is not None and bundle is not None
        workspace_handle = handle
        base_ref = getattr(workspace_handle, "base_ref", None)
        paths = await self._verifier.changed_paths(workspace_handle.path, base_ref)
        validation = validate_candidate(
            Path(workspace_handle.path),
            contract,
            bundle,
            paths,
        )
        if not validation.accepted:
            raise TaskFailed(
                "; ".join(validation.reasons),
                FailureKind.VERIFICATION_ERROR,
            )
        outcomes = await self._verifier.run(
            workspace_handle.path,
            contract.verification_commands,
        )
        self._raise_on_infrastructure_exit(outcomes)
        if not outcomes or any(item.exit_code != 0 for item in outcomes):
            raise TaskFailed(
                "authoritative verification command failed",
                FailureKind.VERIFICATION_ERROR,
            )
        paths = await self._verifier.changed_paths(workspace_handle.path, base_ref)
        validation = validate_candidate(
            Path(workspace_handle.path),
            contract,
            bundle,
            paths,
        )
        if not validation.accepted:
            raise TaskFailed(
                "verification command changed the validated candidate: "
                + "; ".join(validation.reasons),
                FailureKind.VERIFICATION_ERROR,
            )
        candidate_sha = await self._workspace.snapshot(workspace_handle)
        evidence = [
            VerificationEvidence(
                id=new_id(),
                task_id=unit.task_id,
                task_revision=unit.task_revision,
                run_id=unit.execution.run_id,
                candidate_commit_sha=candidate_sha,
                test_commit_sha=bundle.test_commit_sha,
                exact_command=item.command,
                exit_code=item.exit_code,
                started_at=item.started_at,
                finished_at=item.finished_at,
                bounded_output_ref=item.bounded_output_ref,
                verification_kind=VerificationKind.AUTHORITATIVE_TEST,
                accepted=True,
            )
            for item in outcomes
        ]
        if not self._reserve_candidate(plan_id, unit, uow):
            await self._workspace.discard(workspace_handle)
            self._abandon_stale(plan_id, unit, uow)
            return Signal.PAUSED
        await self._workspace.commit(workspace_handle)

        def finalize() -> Signal:
            with uow:
                plan = uow.plans.get(plan_id)
                task = self._unit_task(plan, unit)
                if (
                    plan.goal_promotion_reservations.get(unit.goal_id) != unit.execution.id
                    or task.status != Status.RUNNING
                    or task.revision != unit.task_revision
                    or task.attempt != unit.attempt
                ):
                    self._finish_execution(
                        uow,
                        unit,
                        ExecutionAttemptStatus.ABANDONED,
                        ExecutionRunStatus.ABANDONED,
                    )
                    if plan.goal_promotion_reservations.get(unit.goal_id) == unit.execution.id:
                        plan.release_promotion(unit.goal_id, unit.execution.id)
                        plan.bump_version()
                        uow.plans.save(plan)
                    return Signal.PAUSED
                plan.release_promotion(unit.goal_id, unit.execution.id)
                task.accept_verification(evidence)
                plan.complete_task(
                    unit.goal_id,
                    unit.task_id,
                    TaskResult.success(
                        "orchestrator-owned deterministic verification accepted",
                        metadata={"candidate_commit_sha": candidate_sha},
                    ),
                )
                self._clear_runtime_circuit(uow, unit)
                self._finish_execution(
                    uow,
                    unit,
                    ExecutionAttemptStatus.SUCCEEDED,
                    ExecutionRunStatus.SUCCEEDED,
                )
                paused_at_boundary = self._settle_requested_pause(plan_id, plan, uow)
                plan.bump_version()
                uow.outbox.add(
                    TaskVerificationAccepted(
                        plan_id=plan_id,
                        goal_id=unit.goal_id,
                        task_id=unit.task_id,
                        task_revision=unit.task_revision,
                        evidence_refs=[item.bounded_output_ref for item in evidence],
                    )
                )
                uow.outbox.add(
                    TaskCompleted(
                        plan_id=plan_id,
                        goal_id=unit.goal_id,
                        task_id=unit.task_id,
                    )
                )
                uow.plans.save(plan)
                return Signal.PAUSED if paused_at_boundary else Signal.CONTINUE

        # See _finalize_test_author's identical comment on why re-running the
        # whole block (including _finish_execution) is safe on retry.
        return self._run_with_cas_retry(finalize)

    @staticmethod
    def _run_with_cas_retry(body: Callable[[], _T], max_attempts: int = 5) -> _T:
        """Retry `body` (which opens its own fresh `with uow:` transaction,
        re-fetching current plan/task state each call) on StaleVersionError —
        a concurrent GOAL's write landing in the narrow window between this
        call's own read and write (domain unfreeze #13 / Phase 4: goal-level
        parallelism means more than one worker can legitimately bump the
        same plan's version between this call's get() and save()). `body`
        must be safe to re-run from scratch; every caller here already
        re-derives its state fresh from `uow.plans.get(...)` rather than
        closing over a stale `plan` object, so retrying is exactly "try the
        same idempotent check-and-mutate again on current state."
        Task-identity (status/revision/attempt) is the real fencing token —
        `plan.version` equality was never the right check here and is not
        reintroduced by this retry."""
        for attempt in range(max_attempts):
            try:
                return body()
            except StaleVersionError:
                if attempt == max_attempts - 1:
                    raise
        raise AssertionError("unreachable")  # pragma: no cover

    def _reserve_candidate(self, plan_id: str, unit: _Unit, uow: UnitOfWork) -> bool:
        """Atomically guard the candidate and reserve its Git promotion.
        Retries on StaleVersionError; exhaustion degrades to False (the
        existing "couldn't reserve, abandon this attempt gracefully" path
        every caller already has — safe, since nothing has been mutated yet,
        unlike a task-completion finalize where the real work is precious)."""

        def attempt() -> bool:
            with uow:
                plan = uow.plans.get(plan_id)
                if plan.phase != PlanPhase.RUNNING or plan.status != PlanStatus.RUNNING:
                    return False
                if unit.cycle_id is not None and (
                    plan.active_cycle is None or plan.active_cycle.id != unit.cycle_id
                ):
                    return False
                task = self._unit_task(plan, unit)
                if (
                    task.status != Status.RUNNING
                    or task.revision != unit.task_revision
                    or task.attempt != unit.attempt
                ):
                    return False
                open_attempts = [
                    execution_attempt
                    for execution_attempt in uow.executions.list_open_attempts(plan_id)
                    if execution_attempt.task_id == unit.task_id
                ]
                if not open_attempts:
                    return False
                latest = max(open_attempts, key=lambda execution_attempt: execution_attempt.number)
                if latest.id != unit.execution.id:
                    return False
                plan.reserve_promotion(unit.goal_id, unit.execution.id)
                plan.bump_version()
                uow.plans.save(plan)
                return True

        try:
            return self._run_with_cas_retry(attempt)
        except StaleVersionError:
            return False

    @staticmethod
    def _unit_task(plan: Plan, unit: _Unit) -> Task:
        goals = plan.goals
        if unit.cycle_id is not None:
            cycle = next(
                (item for item in plan.cycles if item.id == unit.cycle_id),
                None,
            )
            if cycle is None:
                raise TaskFailed(
                    f"captured cycle '{unit.cycle_id}' no longer exists",
                    # Class C (the cycle was superseded mid-run; retrying re-races it). Un-freeze #17 made VERIFICATION_ERROR
                    # retryable for candidate rejections; this is not one, so it
                    # keeps the independent `retryable` veto.
                    failure=_orchestration_failure("captured cycle '{unit.cycle_id}' no longer exists"),
                )
            goals = cycle.goals
        return find_task(find_goal(goals, unit.goal_id), unit.task_id)

    def _abandon_stale(self, plan_id: str, unit: _Unit, uow: UnitOfWork) -> None:
        with uow:
            plan = uow.plans.get(plan_id)
            self._finish_execution(
                uow,
                unit,
                ExecutionAttemptStatus.ABANDONED,
                ExecutionRunStatus.ABANDONED,
            )
            task = self._unit_task(plan, unit)
            if (
                (
                    plan.phase != PlanPhase.RUNNING
                    or plan.status != PlanStatus.RUNNING
                    or (
                        unit.cycle_id is not None
                        and (plan.active_cycle is None or plan.active_cycle.id != unit.cycle_id)
                    )
                )
                and task.status == Status.RUNNING
                and task.revision == unit.task_revision
                and task.attempt == unit.attempt
            ):
                plan.abandon_execution_task(unit.cycle_id, unit.goal_id, unit.task_id)
                plan.bump_version()
                uow.outbox.add(
                    TaskAbandoned(
                        plan_id=plan_id,
                        goal_id=unit.goal_id,
                        task_id=unit.task_id,
                        reason="stale worker result rejected",
                    )
                )
                uow.plans.save(plan)

    # ---- txn1 steps (called INSIDE the open transaction) ----

    def _enter_review(self, plan_id: str, plan: Plan, uow: UnitOfWork) -> Signal:
        """Scan exhaustion opens publication review; cyclic roots never terminate."""
        cycle = plan.active_cycle
        if cycle is not None:
            gate = ReviewGate(
                id=new_id(),
                subject_type=ReviewSubjectType.CYCLE_COMPLETION,
                subject_id=cycle.id,
                subject_revision=1,
                allowed_decisions=[
                    "open_pr",
                    "merge",
                    "retain_branch",
                    "discard",
                ],
                continuation="Choose one output disposition for the verified cycle.",
            )
            plan.open_completion_gate(gate, cycle.evidence_refs)
            plan.bump_version()
            uow.outbox.add(
                CycleVerified(
                    plan_id=plan_id,
                    cycle_id=cycle.id,
                    evidence_refs=list(cycle.evidence_refs),
                )
            )
            uow.plans.save(plan)
            return Signal.PAUSED

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

    def _complete_legacy_goal(
        self, plan_id: str, plan: Plan, goal: Goal, uow: UnitOfWork
    ) -> Signal:
        plan.complete_goal(goal.id)
        plan.bump_version()
        uow.outbox.add(GoalCompleted(plan_id=plan_id, goal_id=goal.id))
        uow.plans.save(plan)
        return Signal.CONTINUE

    def _block_on_unpromotable_goal(
        self, plan_id: str, plan: Plan, goal: Goal, failure: TaskFailed, uow: UnitOfWork
    ) -> Signal:
        """A goal navigation selected to close but that cannot merge (a task is
        not DONE or has no accepted evidence — typically a legacy/replan artifact)
        opens a structured block, mirroring `_pause_on_failed_goal`. Without this
        the reservation's TaskFailed escapes `handle()` to the worker loop, which
        re-dispatches and re-raises it every tick (a 1Hz poisoned-plan storm)."""
        offending = next(
            task
            for task in goal.tasks
            if task.status != Status.DONE or not task.verification_evidence
        )
        # Only advertise resolutions that can actually repair this block. `retry`
        # requires a FAILED task (Task.retry rejects skipped/cancelled/terminal ->
        # pending), so a goal wedged by a SKIPPED/evidence-less-DONE task is
        # recoverable only via edit_task or start_replan — offering retry_stage
        # there is a nominal-only resolution that 422s when the operator tries it.
        #
        # Domain unfreeze #14: each goal opens its OWN block now
        # (`Plan.goal_blocks[goal.id]`) -- a different goal's block never
        # collides here (separate dict entries), and `claim_ready_goal`'s
        # candidate scan excludes any goal with an active block from ever
        # being re-selected, so this goal's own block can't collide with
        # itself on a later tick either. No pre-check needed; open_block's
        # "already active" guard is a genuine-bug detector now, not routine.
        resolutions = resolutions_for(
            "execution_failure", task_retryable=offending.status == Status.FAILED
        )
        block = PlanBlock(
            id=new_id(),
            kind="execution_failure",
            explanation=str(failure),
            stage=offending.tdd_stage,
            goal_id=goal.id,
            task_id=offending.id,
            task_revision=offending.revision,
            legal_resolutions=resolutions,
            created_at=self._clock.now(),
        )
        plan.open_block(block)
        uow.outbox.add(
            PlanBlocked(
                plan_id=plan_id,
                block_id=block.id,
                stage=block.stage,
                goal_id=goal.id,
                task_id=offending.id,
                task_revision=offending.revision,
            )
        )
        plan.bump_version()
        uow.plans.save(plan)
        return Signal.PAUSED

    def _reserve_goal_promotion(
        self,
        plan: Plan,
        goal: Goal,
        uow: UnitOfWork,
    ) -> tuple[str, str, str]:
        cycle = plan.active_cycle
        assert cycle is not None
        if not can_promote_goal(goal):
            raise TaskFailed(
                "goal cannot merge without accepted task evidence",
                # Class C (a promotion-time state check, not the agent's work). Un-freeze #17 made VERIFICATION_ERROR
                # retryable for candidate rejections; this is not one, so it
                # keeps the independent `retryable` veto.
                failure=_orchestration_failure("goal cannot merge without accepted task evidence"),
            )
        reservation = f"goal:{cycle.id}:{goal.id}"
        plan.reserve_promotion(goal.id, reservation)
        plan.bump_version()
        uow.plans.save(plan)
        return reservation, cycle.id, goal.id

    def _run_acceptance(
        self,
        plan_id: str,
        cycle_id: str,
        goal_id: str | None,
        trigger: AcceptanceTrigger,
        uow: UnitOfWork,
    ) -> None:
        """Boot the assembled tree and record what happened. ADVISORY ONLY.

        Called at the two points where the cycle branch has just changed
        meaning: after a goal merges (early signal, so a broken application is
        found at goal 2 rather than at publication) and before the publication
        gate opens (the verdict an operator actually decides on).

        Every failure mode here is swallowed on purpose. An acceptance run
        observes; it must never be able to fail a promotion that passed
        verification, or stop a publication gate from opening. `verify()` is
        already contracted not to raise — this is the belt to that adapter's
        braces, because a third-party adapter is exactly the thing that will.
        """
        if self._environment is None or self._environment_context is None:
            return
        ref = cycle_branch(cycle_id)
        try:
            repo, spec = self._environment_context(plan_id)
            verdict = self._environment.verify(repo, ref, spec)
        except Exception as exc:  # noqa: BLE001 - see the docstring
            log.warning("acceptance.adapter_raised", plan_id=plan_id, error=str(exc))
            return
        if not verdict.is_signal:
            # Nothing configured. Recording a row per goal merge saying "nobody
            # asked" is noise, not evidence.
            return
        try:
            with uow:
                uow.acceptance_runs.add(
                    AcceptanceRun(
                        id=new_id(),
                        plan_id=plan_id,
                        cycle_id=cycle_id,
                        goal_id=goal_id,
                        trigger=trigger,
                        ref=ref,
                        outcome=verdict.outcome,
                        summary=verdict.summary,
                        detail=verdict.detail,
                        duration_seconds=verdict.duration_seconds,
                        created_at=self._clock.now(),
                    )
                )
        except Exception as exc:  # noqa: BLE001 - see the docstring
            log.warning("acceptance.record_failed", plan_id=plan_id, error=str(exc))
            return
        log.info(
            "acceptance.recorded",
            plan_id=plan_id,
            cycle_id=cycle_id,
            trigger=trigger,
            outcome=verdict.outcome,
        )

    async def _promote_goal(
        self,
        plan_id: str,
        promotion: tuple[str, str, str],
        uow: UnitOfWork,
    ) -> Signal:
        reservation, cycle_id, goal_id = promotion
        try:
            commit_sha = await self._workspace.merge_goal(plan_id, cycle_id, goal_id)
        except Exception as exc:
            # A verified goal must not be thrown away because the repository was
            # momentarily unusable. A stale worktree registration or a held index
            # lock clears on its own; a CONFLICT never does.
            if self._retry_promotion(plan_id, goal_id, reservation, exc, uow):
                return Signal.NOT_READY
            with uow:
                plan = uow.plans.get(plan_id)
                if plan.goal_promotion_reservations.get(goal_id) != reservation:
                    return Signal.PAUSED
                plan.release_promotion(goal_id, reservation)
                block = PlanBlock(
                    id=new_id(),
                    kind="goal_promotion_failure",
                    explanation=f"goal Git promotion failed: {exc}",
                    stage="merge",
                    goal_id=goal_id,
                    legal_resolutions=resolutions_for("goal_promotion_failure"),
                    created_at=self._clock.now(),
                )
                plan.open_block(block)
                plan.bump_version()
                uow.outbox.add(
                    PlanBlocked(
                        plan_id=plan_id,
                        block_id=block.id,
                        stage=block.stage,
                        goal_id=goal_id,
                    )
                )
                uow.plans.save(plan)
            return Signal.PAUSED

        with uow:
            plan = uow.plans.get(plan_id)
            if plan.goal_promotion_reservations.get(goal_id) != reservation:
                return Signal.PAUSED
            cycle = next(
                (item for item in plan.cycles if item.id == cycle_id),
                None,
            )
            if cycle is None or plan.active_cycle is None or plan.active_cycle.id != cycle_id:
                raise TaskFailed(
                    "goal promotion targets a superseded cycle",
                    # Class C (a promotion race; the agent has nothing to repair). Un-freeze #17 made VERIFICATION_ERROR
                    # retryable for candidate rejections; this is not one, so it
                    # keeps the independent `retryable` veto.
                    failure=_orchestration_failure("goal promotion targets a superseded cycle"),
                )
            goal = find_goal(cycle.goals, goal_id)
            if any(
                task.status != Status.DONE or not task.verification_evidence for task in goal.tasks
            ):
                raise TaskFailed(
                    "goal evidence changed during promotion",
                    # Class C (a promotion race; the agent has nothing to repair). Un-freeze #17 made VERIFICATION_ERROR
                    # retryable for candidate rejections; this is not one, so it
                    # keeps the independent `retryable` veto.
                    failure=_orchestration_failure("goal evidence changed during promotion"),
                )
            cycle.evidence_refs.append(f"git:{commit_sha}")
            # Recorded HERE, not at the merge call: everything above this line
            # in the transaction has already re-guarded the promotion
            # reservation, so a promotion that lost its reservation returned
            # PAUSED without leaving a phantom row. The refs come from the same
            # module the workspace adapter builds its branches from.
            uow.promotions.add(
                GoalPromotion(
                    id=new_id(),
                    plan_id=plan_id,
                    cycle_id=cycle_id,
                    goal_id=goal_id,
                    from_ref=goal_branch(goal_id),
                    into_ref=cycle_branch(cycle_id),
                    merge_sha=commit_sha,
                    promoted_at=self._clock.now(),
                )
            )
            plan.complete_goal(goal_id)
            plan.release_promotion(goal_id, reservation)
            plan.bump_version()
            uow.outbox.add(GoalCompleted(plan_id=plan_id, goal_id=goal_id))
            uow.plans.save(plan)
        # AFTER the transaction closes: booting an application is a side effect,
        # and invariant #5 keeps those out of transactions. Advisory, so a
        # verdict of any kind leaves the promotion above untouched.
        self._run_acceptance(plan_id, cycle_id, goal_id, "goal_merge", uow)
        return Signal.CONTINUE

    def _pause_on_failed_goal(
        self, plan_id: str, plan: Plan, goal: Goal, uow: UnitOfWork
    ) -> Signal:
        """Goal-failure backstop: a cyclic goal whose head task is FAILED opens a
        structured block with executable retry, edit, and replan actions. Normally
        finalization opens the block in the same transaction; this scan branch is
        defensive recovery for persisted legacy/incomplete state."""
        failed = next(task for task in goal.tasks if task.status == Status.FAILED)
        reason = f"goal {goal.id} has a failed task"
        if plan.active_cycle is not None:
            # Domain unfreeze #14: see _block_on_unpromotable_goal's comment —
            # this goal opens its own goal_blocks entry; no pre-check needed.
            block = PlanBlock(
                id=new_id(),
                kind="execution_failure",
                explanation=reason,
                stage=failed.tdd_stage,
                goal_id=goal.id,
                task_id=failed.id,
                task_revision=failed.revision,
                legal_resolutions=resolutions_for("execution_failure"),
                created_at=self._clock.now(),
            )
            plan.open_block(block)
            uow.outbox.add(
                PlanBlocked(
                    plan_id=plan_id,
                    block_id=block.id,
                    stage=block.stage,
                    goal_id=goal.id,
                    task_id=failed.id,
                    task_revision=failed.revision,
                )
            )
        else:
            plan.pause(reason)
            uow.outbox.add(PlanPaused(plan_id=plan_id, reason=reason, auto=True))
        plan.bump_version()
        uow.plans.save(plan)
        return Signal.PAUSED

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
        self,
        plan_id: str,
        plan: Plan,
        goal: Goal,
        task: Task,
        uow: UnitOfWork,
        spec: AgentSpec | None = None,
    ) -> _Unit:
        # resolve agent BEFORE marking running so a missing agent fails fast.
        run_role = _run_role_for(plan, task)
        spec = spec or self._resolve_spec(plan, task)
        was_reclaimed = task.status == Status.RUNNING
        if was_reclaimed:
            for open_attempt in uow.executions.list_open_attempts(plan_id):
                if open_attempt.goal_id == goal.id and open_attempt.task_id == task.id:
                    plan.release_promotion(goal.id, open_attempt.id)
                    uow.executions.finalize_attempt(
                        open_attempt.id,
                        attempt_status=ExecutionAttemptStatus.ABANDONED,
                        run_status=ExecutionRunStatus.ABANDONED,
                        completed_at=self._clock.now(),
                    )
        plan.start_task(goal.id, task.id)
        now = self._clock.now()
        run = None if was_reclaimed else uow.executions.find_active_run(plan_id, goal.id, task.id)
        if run is None:
            run = ExecutionRun(
                id=str(uuid4()),
                plan_id=plan_id,
                goal_id=goal.id,
                task_id=task.id,
                status=ExecutionRunStatus.RUNNING,
                started_at=now,
            )
            uow.executions.add_run(run)
        else:
            uow.executions.mark_run_running(run.id)
        execution = ExecutionAttempt(
            id=str(uuid4()),
            run_id=run.id,
            plan_id=plan_id,
            goal_id=goal.id,
            task_id=task.id,
            number=uow.executions.next_attempt_number(plan_id, goal.id, task.id),
            task_attempt=task.cycle_attempt,
            status=ExecutionAttemptStatus.RUNNING,
            started_at=now,
            last_liveness_at=now,
            runtime=spec.runtime_type,
            provider_id=spec.provider_id,
            model_id=spec.model_id,
        )
        uow.executions.add_attempt(execution)
        unit = _Unit(
            cycle_id=plan.active_cycle.id if plan.active_cycle is not None else None,
            goal_id=goal.id,
            task_id=task.id,
            attempt=task.attempt,
            policy_attempt=task.cycle_attempt,
            task_revision=task.revision,
            retry_policy=plan.retry_policy.model_copy(deep=True),
            task_snapshot=task.model_copy(deep=True),
            spec=spec,
            execution=execution,
            run_role=run_role,
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

    def _resolve_spec(self, plan: Plan, task: Task) -> AgentSpec:
        run_role = _run_role_for(plan, task)
        agent_id = task.role_agent_ids.get(run_role, task.agent_id)
        return (
            self._agents.get(agent_id)
            if agent_id
            else self._agents.get(self._agents.default_agent_id())
        )

    def _provider_metadata(self, spec: AgentSpec) -> tuple[int, CapacityScope]:
        """(in-flight cap, capacity scope) for this spec's provider.

        Cap precedence: model row override -> provider row -> global default. The
        cap is provider DATA because it varies enormously — a paid tier, a free
        aggregator, and a local single-GPU server share no sensible number, and
        one global value would throttle the first or over-drive the last.

        A missing provider row is not fatal here: the runner factory already
        fail-fasts on a broken binding, so falling back keeps this off the
        critical path.
        """
        if self._providers is None or not spec.provider_id:
            return self._capacity.max_inflight, CapacityScope.PER_MODEL
        try:
            provider = self._providers.get(spec.provider_id)
        except DomainError:
            return self._capacity.max_inflight, CapacityScope.PER_MODEL
        scope = resolve_capacity_scope(provider.capacity_scope)
        model = provider.get_model(spec.model_id) if spec.model_id else None
        cap = resolve_max_inflight(
            model_cap=model.max_inflight if model is not None else None,
            provider_cap=provider.max_inflight,
            default=self._capacity.max_inflight,
        )
        return cap, scope

    def _spec_wait_seconds(self, spec: AgentSpec, uow: UnitOfWork) -> float | None:
        """How long this spec's provider is unavailable for, or None if it is free.

        `inf` means "blocked, not merely waiting" (a latched circuit): no amount of
        waiting clears it, so routing elsewhere is always preferable.
        `0.0` means "at its in-flight ceiling right now" — nothing to wait for,
        another pool may well be idle.
        """
        if not spec.provider_id or not spec.model_id:
            return None
        circuit = uow.executions.get_runtime_circuit(
            spec.runtime_type, spec.provider_id, None
        ) or uow.executions.get_runtime_circuit(spec.runtime_type, spec.provider_id, spec.model_id)
        if circuit is not None:
            if circuit.manual_intervention:
                return float("inf")
            remaining = (circuit.retry_at - self._clock.now()).total_seconds()
            if remaining > 0:
                return remaining
        if self._admission_signal(spec, uow) is not None:
            return 0.0
        return None

    def _select_spec(
        self, plan: Plan, task: Task, uow: UnitOfWork
    ) -> AgentSpec:
        """The spec this attempt should actually run on.

        A RUNTIME SUBSTITUTION, never a re-binding: the task's persisted
        `agent_id`/`role_agent_ids` are untouched, because those are the aggregate's
        to own and they record the operator's PREFERENCE. What actually ran is
        recorded on the ExecutionAttempt, which already carries provider/model, so
        every attempt stays auditable.

        Falls back to the preferred spec whenever no better candidate exists, which
        keeps single-agent setups on exactly the path they had before.
        """
        preferred = self._resolve_spec(plan, task)
        wait = self._spec_wait_seconds(preferred, uow)
        if wait is None:
            return preferred  # available: preference wins, no questions asked
        # A short circuit wait is worth waiting out rather than degrading output.
        # An at-capacity provider (wait == 0) substitutes immediately.
        if 0.0 < wait <= self._routing.downgrade_after_seconds:
            return preferred

        required = list(task.required_capabilities)
        candidates = [
            agent
            for agent in self._agents.list()
            if agent.id != preferred.id
            and agent.role == preferred.role
            and set(required).issubset({capability.id for capability in agent.capabilities})
            and self._routing.allows(agent.model_role)
        ]
        candidates.sort(key=lambda agent: self._routing.tier_rank(agent.model_role))
        for candidate in candidates:
            if self._spec_wait_seconds(candidate, uow) is None:
                return candidate
        # Everything capable is throttled: keep the preferred spec so the existing
        # circuit/admission checks produce the same wait or block they always did.
        return preferred

    def _admission_signal(self, spec: AgentSpec, uow: UnitOfWork) -> Signal | None:
        """Refuse to START an attempt that would exceed the provider's in-flight
        ceiling. Turns "fire the 33rd request, get refused, back off" into "never
        fire the 33rd" -- the refusals were never necessary, only unmeasured.

        The count is CROSS-PLAN because the upstream pool is: a per-plan count
        would let two plans each open a full cap.
        """
        if not spec.provider_id or not spec.model_id:
            return None
        cap, scope = self._provider_metadata(spec)
        # An endpoint-wide provider shares one pool across its models, so count
        # every model on it; otherwise each routed model has its own pool.
        counted_model = None if scope is CapacityScope.ENDPOINT_WIDE else spec.model_id
        inflight = uow.executions.count_inflight_attempts(
            spec.runtime_type, spec.provider_id, counted_model
        )
        if inflight >= cap:
            return Signal.NOT_READY
        return None

    def _runtime_circuit_signal(
        self,
        plan_id: str,
        plan: Plan,
        goal: Goal,
        task: Task,
        spec: AgentSpec,
        uow: UnitOfWork,
    ) -> Signal | None:
        if not spec.provider_id or not spec.model_id:
            return None
        # Two circuits can gate one call. The provider-wide one (account-level
        # quota) is checked FIRST because routing to another model cannot escape
        # it; the per-model one covers this model's upstream pool. Whichever is
        # open wins, and a provider-wide block outranks a per-model wait.
        circuit = uow.executions.get_runtime_circuit(
            spec.runtime_type, spec.provider_id, None
        ) or uow.executions.get_runtime_circuit(spec.runtime_type, spec.provider_id, spec.model_id)
        if circuit is None:
            return None
        if circuit.manual_intervention:
            block = PlanBlock(
                id=new_id(),
                kind="provider_capacity",
                explanation=circuit.safe_message,
                stage=task.tdd_stage,
                goal_id=goal.id,
                task_id=task.id,
                task_revision=task.revision,
                legal_resolutions=resolutions_for("provider_capacity"),
                evidence_refs=[circuit_ref(circuit.runtime, circuit.provider_id, circuit.model_id)],
                created_at=self._clock.now(),
            )
            plan.open_block(block)
            plan.bump_version()
            uow.outbox.add(
                PlanBlocked(
                    plan_id=plan_id,
                    block_id=block.id,
                    stage=block.stage,
                    goal_id=goal.id,
                    task_id=task.id,
                    task_revision=task.revision,
                )
            )
            uow.plans.save(plan)
            return Signal.PAUSED
        now = self._clock.now()
        if now < circuit.retry_at:
            return Signal.NOT_READY
        # HALF-OPEN PROBE, SINGLE-FLIGHT. Past retry_at exactly one runner may
        # proceed. This used to be a bare `return None`, which every concurrent
        # goal worker reached at once: with four goals in flight, one outage
        # window cost four failures instead of one and inflated the circuit four
        # times as fast as the outage warranted. The claim is a conditional UPDATE
        # inside this transaction, so the losers see it taken and keep waiting.
        # The record is kept until success so a failed probe increments the
        # durable failure count instead of silently resetting the circuit.
        if not uow.executions.try_claim_circuit_probe(
            circuit.runtime,
            circuit.provider_id,
            circuit.model_id,
            holder=f"{plan_id}:{goal.id}:{task.id}:{task.attempt}",
            now=now,
            stale_before=now - timedelta(seconds=self._capacity.probe_stale_after_seconds),
        ):
            return Signal.NOT_READY
        return None

    # ---- finalize transactions (each opens its own txn) ----

    def _abandoned_task_status(self, plan: Plan, unit: _Unit) -> Task | None:
        """If the plan left RUNNING (mid-flight replan), return the task for the
        tolerant-finalize checks; None means the normal path applies."""
        cycle_superseded = unit.cycle_id is not None and (
            plan.active_cycle is None or plan.active_cycle.id != unit.cycle_id
        )
        if (
            plan.phase == PlanPhase.RUNNING
            and plan.status == PlanStatus.RUNNING
            and not cycle_superseded
        ):
            return None
        return self._unit_task(plan, unit)

    @staticmethod
    def _clear_runtime_circuit(uow: UnitOfWork, unit: _Unit) -> None:
        """A successful run proves the provider recovered — retire its circuit.

        Must be called from EVERY success finalizer. It used to live only in
        `_finalize_success` (the legacy, non-cyclic path), so a cyclic plan
        never cleared it: `failure_count` accumulated transient rate limits
        across an entire run until it latched `manual_intervention`, opening a
        provider_capacity block that only a human `wait_and_retry` could reset.
        """
        if unit.spec.provider_id and unit.spec.model_id:
            # Clear BOTH tiers: a completed run proves the account has budget and
            # this model's upstream pool has room. Leaving the provider-wide row
            # behind would keep every sibling model gated on a resolved outage.
            uow.executions.clear_runtime_circuit(
                unit.spec.runtime_type,
                unit.spec.provider_id,
                unit.spec.model_id,
            )
            uow.executions.clear_runtime_circuit(
                unit.spec.runtime_type,
                unit.spec.provider_id,
                None,
            )

    def _finish_execution(
        self,
        uow: UnitOfWork,
        unit: _Unit,
        attempt_status: ExecutionAttemptStatus,
        run_status: ExecutionRunStatus,
        *,
        failure: RuntimeFailure | None = None,
        retry_at: datetime | None = None,
        stdout_tail: str = "",
        stderr_tail: str = "",
    ) -> None:
        uow.executions.finalize_attempt(
            unit.execution.id,
            attempt_status=attempt_status,
            run_status=run_status,
            completed_at=self._clock.now(),
            failure=failure,
            retry_at=retry_at,
            stdout_tail=stdout_tail,
            stderr_tail=stderr_tail,
        )

    @staticmethod
    def _jitter_unit(attempt_id: str) -> float:
        digest = hashlib.sha256(attempt_id.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") / float(2**64 - 1)

    def _settle_requested_pause(self, plan_id: str, plan: Plan, uow: UnitOfWork) -> bool:
        if not plan.pause_requested:
            return False
        reason = plan.paused_reason
        plan.settle_pause()
        uow.outbox.add(PlanPaused(plan_id=plan_id, reason=reason, auto=False))
        return True

    def _finalize_failure(
        self, plan_id: str, unit: _Unit, exc: TaskFailed, uow: UnitOfWork
    ) -> Signal:
        def finalize() -> Signal:
            # A CAS retry re-runs this whole closure, so anything queued by the
            # abandoned attempt must go with it or the repair is recorded twice.
            self._pending_artifacts.clear()
            with uow:
                plan = uow.plans.get(plan_id)
                plan.release_promotion(unit.goal_id, unit.execution.id)

                # TOLERANT FINALIZE: the iteration was abandoned while we ran — a late
                # failure terminal-skips; it must NEVER requeue into the abandoned
                # iteration (the resurrection bug).
                abandoned = self._abandoned_task_status(plan, unit)
                if abandoned is not None:
                    self._finish_execution(
                        uow,
                        unit,
                        ExecutionAttemptStatus.ABANDONED,
                        ExecutionRunStatus.ABANDONED,
                    )
                    if not abandoned.is_terminal:
                        plan.abandon_execution_task(
                            unit.cycle_id,
                            unit.goal_id,
                            unit.task_id,
                        )
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

                delay = capacity_backoff_seconds(
                    unit.retry_policy,
                    unit.policy_attempt + 1,
                    jitter_unit=self._jitter_unit(unit.execution.id),
                    kind=exc.kind,
                    # A concurrency refusal does not escalate like an exhausted
                    # allowance; every other scope keeps the patient curve.
                    limit_scope=exc.failure.limit_scope,
                )
                if exc.failure.retry_after_seconds is not None:
                    delay = max(delay, exc.failure.retry_after_seconds)
                if (
                    exc.failure.limit_scope is not None
                    and exc.failure.limit_scope.value == "daily_quota"
                    and exc.failure.retry_after_seconds is None
                ):
                    delay = max(delay, 3_600.0)
                not_before = self._clock.now() + timedelta(seconds=delay) if delay > 0 else None

                circuit_manual = False
                # True only while a capacity outage is being ridden out on
                # automatic waiting; a REQUEST_CONCURRENCY refusal opens no
                # circuit and so keeps its ordinary per-task budget as the bound.
                capacity_wait = False
                # The circuit this failure was recorded against, if any -- carried
                # to the block below so its evidence ref names the row that exists.
                capacity_ref: str | None = None
                if (
                    # CONNECTION_ERROR is capacity too: an unreachable or
                    # overloaded endpoint is not a defect in this task, and
                    # exhausting the per-task budget against it used to open a
                    # goal block for something no human edit could fix. Safe to
                    # widen only now that the ceiling above bounds the wait --
                    # doing it while the attempt-count latch was live would have
                    # escalated after five shared failures instead.
                    exc.kind in CAPACITY_KINDS
                    and unit.spec.provider_id
                    and unit.spec.model_id
                    and not_before is not None
                    # A CONCURRENCY cap is not an outage: the provider served
                    # every other in-flight request successfully and refused only
                    # the one over the ceiling. The remedy is "send fewer at
                    # once", so this attempt requeues on its own short backoff and
                    # NO circuit opens -- opening one would halt a provider that
                    # is working, and stall the siblings that are mid-run.
                    # The failure is still recorded on the attempt for telemetry.
                    and exc.failure.limit_scope is not LimitScope.REQUEST_CONCURRENCY
                ):
                    # Account-level limits key provider-wide; upstream-level ones
                    # key per model. Routing to a sibling model escapes the second
                    # but never the first.
                    _, capacity_scope = self._provider_metadata(unit.spec)
                    circuit_model = circuit_model_id(
                        unit.spec.model_id, exc.failure.limit_scope, capacity_scope
                    )
                    capacity_ref = circuit_ref(
                        unit.spec.runtime_type, unit.spec.provider_id, circuit_model
                    )
                    existing = uow.executions.get_runtime_circuit(
                        unit.spec.runtime_type,
                        unit.spec.provider_id,
                        circuit_model,
                    )
                    failure_count = (existing.failure_count if existing else 0) + 1
                    # The START of the outage, preserved across updates within one
                    # outage (restamping every failure would peg the age at ~0 and
                    # make the ceiling unreachable) but RESET when the previous
                    # circuit has been quiet long enough to count as a past outage.
                    # A row abandoned by an earlier session would otherwise pre-age
                    # a fresh outage straight past its ceiling — observed live
                    # against a circuit left behind three days earlier.
                    scope_value = (
                        exc.failure.limit_scope.value
                        if exc.failure.limit_scope is not None
                        else None
                    )
                    opened_at = self._capacity.outage_start(
                        existing.opened_at if existing else None,
                        existing.retry_at if existing else None,
                        self._clock.now(),
                        scope_value,
                    )
                    # ESCALATE ON DURATION, NOT ON A COUNT. The old rule compared
                    # this provider-global `failure_count` against a PER-TASK
                    # attempt budget -- different units of measure. It latched
                    # after a handful of failures shared across concurrent goals
                    # while each task had barely retried, and it ignored the
                    # operator's raised `retry_max_attempts` entirely (see
                    # RetryPolicy.should_retry, which deliberately treats that key
                    # as a floor). `failure_count` is kept for telemetry only.
                    circuit_manual = self._capacity.outage_exceeded(
                        opened_at, self._clock.now(), scope_value
                    )
                    uow.executions.upsert_runtime_circuit(
                        RuntimeCircuit(
                            runtime=unit.spec.runtime_type,
                            provider_id=unit.spec.provider_id,
                            model_id=circuit_model,
                            failure_count=failure_count,
                            opened_at=opened_at,
                            retry_at=not_before,
                            last_failure_kind=exc.kind.value,
                            safe_message=exc.reason,
                            manual_intervention=circuit_manual,
                            limit_scope=scope_value,
                            # probe_holder/probe_started_at default to None, so
                            # rewriting the row RELEASES this attempt's probe --
                            # load-bearing, and the reason the next window is
                            # probeable at all.
                        )
                    )
                    # Inside the ceiling a capacity failure must ALSO bypass the
                    # per-task retry budget. Removing only the circuit latch would
                    # have changed nothing: the task still exhausted
                    # kind_max_attempts and reached fail_task, opening the same
                    # goal block a few attempts later. Waiting is bounded by the
                    # ceiling above, which is the whole point of the redesign.
                    capacity_wait = not circuit_manual
                elif unit.spec.provider_id and unit.spec.model_id:
                    # This attempt may have held the half-open probe and then
                    # failed for a NON-capacity reason (the provider answered; the
                    # run failed on its own merits). Release the probe explicitly
                    # so the next window is probeable immediately instead of
                    # waiting out the stale timeout.
                    for probe_key in (unit.spec.model_id, None):
                        uow.executions.release_circuit_probe(
                            unit.spec.runtime_type, unit.spec.provider_id, probe_key
                        )

                if (
                    exc.failure.retryable
                    and not circuit_manual
                    and (
                        capacity_wait
                        or unit.retry_policy.should_retry(
                            self._attempts_against_budget(plan_id, unit, exc.kind, uow),
                            exc.kind,
                        )
                    )
                ):
                    plan.requeue_task(unit.goal_id, unit.task_id, not_before)
                    self._finish_execution(
                        uow,
                        unit,
                        ExecutionAttemptStatus.FAILED,
                        ExecutionRunStatus.RETRYING,
                        failure=exc.failure,
                        retry_at=not_before,
                    )
                    paused_at_boundary = self._settle_requested_pause(plan_id, plan, uow)
                    plan.bump_version()
                    uow.outbox.add(
                        TaskRequeued(
                            plan_id=plan_id,
                            goal_id=unit.goal_id,
                            task_id=unit.task_id,
                            attempt=unit.attempt,
                            reason=exc.reason,
                            kind=exc.kind.value if exc.kind else None,
                        )
                    )
                    uow.plans.save(plan)
                    return Signal.PAUSED if paused_at_boundary else Signal.CONTINUE

                # Before giving up on the operator's behalf: is the CONTRACT the
                # thing that is wrong? A contract no agent could satisfy fails
                # identically on every attempt, so blocking a human is the only
                # outcome a retry can reach — unless the contract itself is fixed.
                if self._repair_contract(plan_id, plan, unit, exc, uow, not_before):
                    return Signal.CONTINUE

                # Terminal task failure (retry budget exhausted or non-retryable kind):
                # record the FAILED task and AUTO-PAUSE the plan in the same txn
                # (un-freeze #3) — recoverable via edit-while-paused + resume, instead
                # of the old terminal plan FAILED.
                reason = f"task {unit.task_id} failed after {unit.policy_attempt} policy attempt(s): {exc.reason}"
                plan.fail_task(unit.goal_id, unit.task_id, exc.reason, exc.kind)
                self._finish_execution(
                    uow,
                    unit,
                    ExecutionAttemptStatus.FAILED,
                    ExecutionRunStatus.FAILED,
                    failure=exc.failure,
                )
                uow.outbox.add(
                    TaskFailedEvent(
                        plan_id=plan_id,
                        goal_id=unit.goal_id,
                        task_id=unit.task_id,
                        reason=exc.reason,
                        kind=exc.kind.value if exc.kind else None,
                    )
                )
                if plan.active_cycle is not None:
                    # Domain unfreeze #14: this goal opens its own
                    # goal_blocks entry -- a different goal's concurrently
                    # opened block never collides here (separate dict
                    # entries; before #13 this branch had to no-op and drop
                    # this goal's own failure reason when ANY block was
                    # already active anywhere in the plan).
                    # Any capacity kind that latched, not just RATE_LIMIT: a
                    # CONNECTION_ERROR outage past the ceiling is equally a
                    # provider problem, and labelling it execution_failure would
                    # advertise retry_stage instead of wait_and_retry, leaving the
                    # operator no way to clear the circuit.
                    provider_capacity = circuit_manual and exc.kind in CAPACITY_KINDS
                    block = PlanBlock(
                        id=new_id(),
                        kind=("provider_capacity" if provider_capacity else "execution_failure"),
                        explanation=reason,
                        stage=plan._task(plan._goal(unit.goal_id), unit.task_id).tdd_stage,
                        goal_id=unit.goal_id,
                        task_id=unit.task_id,
                        task_revision=unit.task_revision,
                        run_id=unit.execution.run_id,
                        legal_resolutions=resolutions_for(
                            "provider_capacity" if provider_capacity else "execution_failure"
                        ),
                        evidence_refs=(
                            # MUST name the circuit that was actually written --
                            # which for an account-level limit is the PROVIDER-WIDE
                            # key, not this model's. Hand-building it from
                            # spec.model_id pointed wait_and_retry at a row that
                            # does not exist, so the real circuit stayed latched.
                            [f"execution-attempt://{unit.execution.id}", capacity_ref]
                            if provider_capacity and capacity_ref is not None
                            else [f"execution-attempt://{unit.execution.id}"]
                        ),
                        created_at=self._clock.now(),
                    )
                    plan.open_block(block)
                    uow.outbox.add(
                        PlanBlocked(
                            plan_id=plan_id,
                            block_id=block.id,
                            stage=block.stage,
                            goal_id=unit.goal_id,
                            task_id=unit.task_id,
                            task_revision=unit.task_revision,
                            run_id=unit.execution.run_id,
                        )
                    )
                # A human pause landing during a legacy in-flight attempt keeps its
                # manual semantics. Cyclic failures instead become explicit blocks.
                elif plan.pause_requested:
                    self._settle_requested_pause(plan_id, plan, uow)
                elif not plan.paused:
                    plan.pause(reason)
                    uow.outbox.add(PlanPaused(plan_id=plan_id, reason=reason, auto=True))
                plan.bump_version()
                uow.plans.save(plan)
                return Signal.PAUSED

        # See _finalize_test_author's identical comment on why re-running
        # the whole block (including _finish_execution / open_block) is
        # safe on retry -- the with-uow block rolls back atomically.
        try:
            return self._run_with_cas_retry(finalize)
        finally:
            # `finally`, not the success path: the artifact records WHY an
            # attempt failed, so it is worth most when finalize itself did not
            # complete. The store's own contract is best-effort and
            # rollback-independent.
            self._flush_pending_artifacts()

    def _finalize_success(
        self, plan_id: str, unit: _Unit, result: TaskResult, uow: UnitOfWork
    ) -> Signal:
        def finalize() -> Signal:
            # ---- txn2: persist result + DONE atomically with the event ----
            with uow:
                plan = uow.plans.get(plan_id)

                if unit.cycle_id is not None and (
                    plan.active_cycle is None or plan.active_cycle.id != unit.cycle_id
                ):
                    superseded = self._unit_task(plan, unit)
                    self._finish_execution(
                        uow,
                        unit,
                        ExecutionAttemptStatus.ABANDONED,
                        ExecutionRunStatus.ABANDONED,
                    )
                    plan.release_promotion(unit.goal_id, unit.execution.id)
                    if not superseded.is_terminal:
                        plan.abandon_execution_task(
                            unit.cycle_id,
                            unit.goal_id,
                            unit.task_id,
                        )
                        plan.bump_version()
                        uow.outbox.add(
                            TaskAbandoned(
                                plan_id=plan_id,
                                goal_id=unit.goal_id,
                                task_id=unit.task_id,
                                reason="cycle superseded while task was running",
                            )
                        )
                        uow.plans.save(plan)
                    return Signal.PAUSED

                # TOLERANT FINALIZE (success side): if the finalize-abandon already
                # closed this task, drop the late result (harmless — the iteration is
                # abandoned). If the task is still RUNNING, complete it normally:
                # a late success is harmless history for the next re-plan.
                abandoned = self._abandoned_task_status(plan, unit)
                if abandoned is not None and abandoned.is_terminal:
                    self._finish_execution(
                        uow,
                        unit,
                        ExecutionAttemptStatus.ABANDONED,
                        ExecutionRunStatus.ABANDONED,
                    )
                    return Signal.PAUSED

                if plan.goal_promotion_reservations.get(unit.goal_id) != unit.execution.id:
                    self._finish_execution(
                        uow,
                        unit,
                        ExecutionAttemptStatus.ABANDONED,
                        ExecutionRunStatus.ABANDONED,
                    )
                    return Signal.PAUSED
                plan.release_promotion(unit.goal_id, unit.execution.id)
                plan.complete_task(unit.goal_id, unit.task_id, result)
                self._clear_runtime_circuit(uow, unit)
                self._finish_execution(
                    uow,
                    unit,
                    ExecutionAttemptStatus.SUCCEEDED,
                    ExecutionRunStatus.SUCCEEDED,
                    stdout_tail=safe_runtime_tail(result.output),
                )
                paused_at_boundary = self._settle_requested_pause(plan_id, plan, uow)
                plan.bump_version()
                uow.outbox.add(
                    TaskCompleted(plan_id=plan_id, goal_id=unit.goal_id, task_id=unit.task_id)
                )
                uow.plans.save(plan)
                return Signal.PAUSED if paused_at_boundary else Signal.CONTINUE

        # See _finalize_test_author's identical comment on why re-running
        # the whole block is safe on retry.
        return self._run_with_cas_retry(finalize)
