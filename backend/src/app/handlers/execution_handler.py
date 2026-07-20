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
from uuid import uuid4

from src.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from src.domain.entities.agent_spec import AgentSpec
from src.domain.entities.execution_contracts import (
    TestBundle,
    VerificationEvidence,
    VerificationKind,
    VerificationStrategy,
)
from src.domain.entities.goal import Goal
from src.domain.entities.planning_artifacts import (
    PlanBlock,
    PlanStatus,
    ReviewGate,
    ReviewSubjectType,
)
from src.domain.entities.task import Task
from src.domain.events.outbox import (
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
from src.domain.factories.identity import new_id
from src.domain.policies.retry_policies import RetryPolicy
from src.domain.repositories.agent_repo import AgentRepository
from src.domain.services.lookups import find_goal, find_task
from src.domain.services.navigation import NOT_READY
from src.domain.value_objects.lifecycle import Status
from src.domain.value_objects.lifecycle import FailureKind
from src.domain.value_objects.tasks_vos import TaskResult

from src.app.execution_records import (
    ExecutionAttempt,
    ExecutionAttemptStatus,
    ExecutionRun,
    ExecutionRunStatus,
    RuntimeCircuit,
)
from src.app.runtime_failures import RuntimeFailure, safe_runtime_tail
from src.app.handlers.base import Signal
from src.app.ports import (
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
from src.app.verification import sha256_file, validate_candidate


@dataclass(frozen=True)
class _Unit:
    """Revision-bound values captured in the start transaction."""

    cycle_id: str | None
    goal_id: str
    task_id: str
    attempt: int
    policy_attempt: int
    task_revision: int
    plan_version: int
    retry_policy: RetryPolicy
    task_snapshot: Task
    spec: AgentSpec
    execution: ExecutionAttempt
    run_role: str


class ExecutionHandler:
    def __init__(
        self,
        runner: AgentRunner,
        agents: AgentRepository,
        workspace: Workspace,
        event_sink: AgentEventSink,
        clock: Clock,
        verifier: VerificationExecutor | None = None,
    ) -> None:
        self._runner = runner
        self._agents = agents
        self._workspace = workspace
        self._event_sink = event_sink
        self._clock = clock
        self._verifier = verifier

    async def handle(self, plan_id: str, plan: Plan, uow: UnitOfWork) -> Signal:
        # ---- txn1: pick the unit, mark RUNNING, persist + outbox atomically ----
        goal_promotion: tuple[str, str, str] | None = None
        unit: _Unit | None = None
        with uow:
            plan = uow.plans.get(plan_id)
            if plan.paused or plan.pause_requested:
                return Signal.PAUSED  # pause gate armed while we were dispatched
            action = plan.peek_next(self._clock.now())

            if action is None:
                return self._enter_review(plan_id, plan, uow)
            if action == NOT_READY:
                return Signal.NOT_READY

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
                    return self._block_on_unpromotable_goal(
                        plan_id, plan, goal, failure, uow
                    )
            else:
                if second == "GOAL_FAILED":
                    return self._pause_on_failed_goal(plan_id, plan, goal, uow)
                if second == "DEPENDENCY_BLOCKED":
                    return Signal.NOT_READY

                task = second
                # check-before-act: result already exists -> finalize without re-running.
                if task.result is not None:
                    return self._finalize_existing(plan_id, plan, goal, task, uow)

                spec = self._resolve_spec(plan, task)
                circuit_signal = self._runtime_circuit_signal(plan_id, plan, goal, task, spec, uow)
                if circuit_signal is not None:
                    return circuit_signal
                unit = self._start_unit(plan_id, plan, goal, task, uow, spec)

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
        try:
            main_repo_before = await self._main_repo_status()
            result: TaskResult = await self._runner.run(
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
                        FailureKind.VERIFICATION_ERROR,
                    )
                if unit.run_role == "test_author":
                    return await self._finalize_test_author(plan_id, unit, handle, uow)
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
        normalized = path.replace("\\", "/")
        if strategy == VerificationStrategy.EXECUTABLE_CHECK:
            return True
        name = normalized.rsplit("/", 1)[-1]
        return (
            normalized.startswith("tests/")
            or "/tests/" in normalized
            or name.startswith("test_")
            or name in {"conftest.py", "pytest.ini"}
        )

    async def _finalize_test_author(
        self,
        plan_id: str,
        unit: _Unit,
        handle: WorkspaceHandle,
        uow: UnitOfWork,
    ) -> Signal:
        assert self._verifier is not None
        contract = unit.task_snapshot.contract
        assert contract is not None
        workspace_handle = handle
        base_ref = getattr(workspace_handle, "base_ref", None)
        paths = await self._verifier.changed_paths(workspace_handle.path, base_ref)
        if not paths:
            raise TaskFailed(
                "test author produced no executable checks",
                FailureKind.VERIFICATION_ERROR,
            )
        disallowed = [
            path
            for path in paths
            if not self._test_author_path_allowed(path, contract.verification_strategy)
        ]
        if disallowed:
            raise TaskFailed(
                f"test author modified production paths: {disallowed}",
                FailureKind.VERIFICATION_ERROR,
            )
        outcomes = await self._verifier.run(
            workspace_handle.path,
            contract.verification_commands,
        )
        paths = await self._verifier.changed_paths(workspace_handle.path, base_ref)
        disallowed = [
            path
            for path in paths
            if not self._test_author_path_allowed(path, contract.verification_strategy)
        ]
        if disallowed:
            raise TaskFailed(
                f"verification command modified production paths: {disallowed}",
                FailureKind.VERIFICATION_ERROR,
            )
        self._raise_on_infrastructure_exit(outcomes)
        if contract.verification_strategy == VerificationStrategy.TDD:
            valid_baseline = bool(outcomes) and any(item.exit_code != 0 for item in outcomes)
        else:
            valid_baseline = bool(outcomes) and all(item.exit_code == 0 for item in outcomes)
        if not valid_baseline:
            expected = (
                "a meaningful RED result"
                if contract.verification_strategy == VerificationStrategy.TDD
                else "a passing characterization/check baseline"
            )
            raise TaskFailed(
                f"test bundle did not establish {expected}",
                FailureKind.VERIFICATION_ERROR,
            )
        missing = [path for path in paths if not (Path(workspace_handle.path) / path).is_file()]
        if missing:
            raise TaskFailed(
                f"test author deleted or renamed executable checks: {missing}",
                FailureKind.VERIFICATION_ERROR,
            )
        protected = {path: sha256_file(Path(workspace_handle.path) / path) for path in paths}
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
            criterion_to_tests={
                criterion.id: list(paths) for criterion in contract.acceptance_criteria
            },
            verification_strategy=contract.verification_strategy,
            baseline_evidence_refs=[],
            red_or_baseline_evidence_refs=evidence_refs,
            frozen_at=self._clock.now(),
        )
        with uow:
            plan = uow.plans.get(plan_id)
            task = self._unit_task(plan, unit)
            if (
                plan.promotion_reservation != unit.execution.id
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
                if plan.promotion_reservation == unit.execution.id:
                    plan.release_promotion(unit.execution.id)
                    plan.bump_version()
                    uow.plans.save(plan)
                return Signal.PAUSED
            plan.release_promotion(unit.execution.id)
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
        with uow:
            plan = uow.plans.get(plan_id)
            task = self._unit_task(plan, unit)
            if (
                plan.promotion_reservation != unit.execution.id
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
                if plan.promotion_reservation == unit.execution.id:
                    plan.release_promotion(unit.execution.id)
                    plan.bump_version()
                    uow.plans.save(plan)
                return Signal.PAUSED
            plan.release_promotion(unit.execution.id)
            task.accept_verification(evidence)
            plan.complete_task(
                unit.goal_id,
                unit.task_id,
                TaskResult.success(
                    "orchestrator-owned deterministic verification accepted",
                    metadata={"candidate_commit_sha": candidate_sha},
                ),
            )
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

    def _candidate_is_current(self, plan_id: str, unit: _Unit, uow: UnitOfWork) -> bool:
        """Re-read every identity that authorizes a branch merge/finalize."""
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
            safe_pause_version = plan.pause_requested and plan.version == unit.plan_version + 1
            if plan.version != unit.plan_version and not safe_pause_version:
                return False
            open_attempts = [
                attempt
                for attempt in uow.executions.list_open_attempts(plan_id)
                if attempt.task_id == unit.task_id
            ]
            if not open_attempts:
                return False
            latest = max(open_attempts, key=lambda attempt: attempt.number)
            return latest.id == unit.execution.id

    def _reserve_candidate(self, plan_id: str, unit: _Unit, uow: UnitOfWork) -> bool:
        """Atomically guard the candidate and reserve its Git promotion."""
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
            safe_pause_version = plan.pause_requested and plan.version == unit.plan_version + 1
            if plan.version != unit.plan_version and not safe_pause_version:
                return False
            open_attempts = [
                attempt
                for attempt in uow.executions.list_open_attempts(plan_id)
                if attempt.task_id == unit.task_id
            ]
            if not open_attempts:
                return False
            latest = max(open_attempts, key=lambda attempt: attempt.number)
            if latest.id != unit.execution.id:
                return False
            plan.reserve_promotion(unit.execution.id)
            plan.bump_version()
            uow.plans.save(plan)
            return True

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
                    FailureKind.VERIFICATION_ERROR,
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
        block = PlanBlock(
            id=new_id(),
            kind="execution_failure",
            explanation=str(failure),
            stage=offending.tdd_stage,
            goal_id=goal.id,
            task_id=offending.id,
            task_revision=offending.revision,
            legal_resolutions=["retry_stage", "edit_task", "start_replan"],
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
        if any(task.status != Status.DONE or not task.verification_evidence for task in goal.tasks):
            raise TaskFailed(
                "goal cannot merge without accepted task evidence",
                FailureKind.VERIFICATION_ERROR,
            )
        reservation = f"goal:{cycle.id}:{goal.id}"
        plan.reserve_promotion(reservation)
        plan.bump_version()
        uow.plans.save(plan)
        return reservation, cycle.id, goal.id

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
            with uow:
                plan = uow.plans.get(plan_id)
                if plan.promotion_reservation != reservation:
                    return Signal.PAUSED
                plan.release_promotion(reservation)
                block = PlanBlock(
                    id=new_id(),
                    kind="goal_promotion_failure",
                    explanation=f"goal Git promotion failed: {exc}",
                    stage="merge",
                    goal_id=goal_id,
                    legal_resolutions=["start_replan"],
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
            if plan.promotion_reservation != reservation:
                return Signal.PAUSED
            cycle = next(
                (item for item in plan.cycles if item.id == cycle_id),
                None,
            )
            if cycle is None or plan.active_cycle is None or plan.active_cycle.id != cycle_id:
                raise TaskFailed(
                    "goal promotion targets a superseded cycle",
                    FailureKind.VERIFICATION_ERROR,
                )
            goal = find_goal(cycle.goals, goal_id)
            if any(
                task.status != Status.DONE or not task.verification_evidence for task in goal.tasks
            ):
                raise TaskFailed(
                    "goal evidence changed during promotion",
                    FailureKind.VERIFICATION_ERROR,
                )
            cycle.evidence_refs.append(f"git:{commit_sha}")
            plan.complete_goal(goal_id)
            plan.release_promotion(reservation)
            plan.bump_version()
            uow.outbox.add(GoalCompleted(plan_id=plan_id, goal_id=goal_id))
            uow.plans.save(plan)
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
            block = PlanBlock(
                id=new_id(),
                kind="execution_failure",
                explanation=reason,
                stage=failed.tdd_stage,
                goal_id=goal.id,
                task_id=failed.id,
                task_revision=failed.revision,
                legal_resolutions=[
                    "retry_stage",
                    "edit_task",
                    "start_replan",
                ],
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
        run_role = (
            "test_author"
            if plan.active_cycle is not None
            and task.contract is not None
            and (task.test_bundle is None or not task.test_bundle.validates(task.id, task.revision))
            else "implementer"
        )
        spec = spec or self._resolve_spec(plan, task)
        was_reclaimed = task.status == Status.RUNNING
        if was_reclaimed:
            for open_attempt in uow.executions.list_open_attempts(plan_id):
                if open_attempt.goal_id == goal.id and open_attempt.task_id == task.id:
                    plan.release_promotion(open_attempt.id)
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
            plan_version=plan.version + 1,
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
        run_role = (
            "test_author"
            if plan.active_cycle is not None
            and task.contract is not None
            and (task.test_bundle is None or not task.test_bundle.validates(task.id, task.revision))
            else "implementer"
        )
        agent_id = task.role_agent_ids.get(run_role, task.agent_id)
        return (
            self._agents.get(agent_id)
            if agent_id
            else self._agents.get(self._agents.default_agent_id())
        )

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
        circuit = uow.executions.get_runtime_circuit(
            spec.runtime_type, spec.provider_id, spec.model_id
        )
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
                legal_resolutions=[
                    "wait_and_retry",
                    "edit_task",
                    "start_replan",
                ],
                evidence_refs=[
                    f"runtime-circuit://{circuit.runtime}/{circuit.provider_id}/{circuit.model_id}"
                ],
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
        if self._clock.now() < circuit.retry_at:
            return Signal.NOT_READY
        # Half-open probe: one invocation may proceed after the persisted window.
        # Keep the record until success so a failed probe increments the durable
        # failure count instead of silently resetting the circuit.
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
        with uow:
            plan = uow.plans.get(plan_id)
            plan.release_promotion(unit.execution.id)

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

            delay = unit.retry_policy.backoff_for(
                unit.policy_attempt + 1,
                jitter_unit=self._jitter_unit(unit.execution.id),
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
            if (
                exc.kind == FailureKind.RATE_LIMIT
                and unit.spec.provider_id
                and unit.spec.model_id
                and not_before is not None
            ):
                existing = uow.executions.get_runtime_circuit(
                    unit.spec.runtime_type,
                    unit.spec.provider_id,
                    unit.spec.model_id,
                )
                failure_count = (existing.failure_count if existing else 0) + 1
                circuit_manual = failure_count >= unit.retry_policy.max_attempts
                uow.executions.upsert_runtime_circuit(
                    RuntimeCircuit(
                        runtime=unit.spec.runtime_type,
                        provider_id=unit.spec.provider_id,
                        model_id=unit.spec.model_id,
                        failure_count=failure_count,
                        opened_at=self._clock.now(),
                        retry_at=not_before,
                        last_failure_kind=exc.kind.value,
                        safe_message=exc.reason,
                        manual_intervention=circuit_manual,
                    )
                )

            if (
                exc.failure.retryable
                and not circuit_manual
                and unit.retry_policy.should_retry(unit.policy_attempt, exc.kind)
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
                provider_capacity = circuit_manual and exc.kind == FailureKind.RATE_LIMIT
                block = PlanBlock(
                    id=new_id(),
                    kind=("provider_capacity" if provider_capacity else "execution_failure"),
                    explanation=reason,
                    stage=plan._task(plan._goal(unit.goal_id), unit.task_id).tdd_stage,
                    goal_id=unit.goal_id,
                    task_id=unit.task_id,
                    task_revision=unit.task_revision,
                    run_id=unit.execution.run_id,
                    legal_resolutions=(
                        ["wait_and_retry", "edit_task", "start_replan"]
                        if provider_capacity
                        else ["retry_stage", "edit_task", "start_replan"]
                    ),
                    evidence_refs=(
                        [
                            f"execution-attempt://{unit.execution.id}",
                            "runtime-circuit://"
                            f"{unit.spec.runtime_type}/{unit.spec.provider_id}/{unit.spec.model_id}",
                        ]
                        if provider_capacity
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

    def _finalize_success(
        self, plan_id: str, unit: _Unit, result: TaskResult, uow: UnitOfWork
    ) -> Signal:
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
                plan.release_promotion(unit.execution.id)
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

            if plan.promotion_reservation != unit.execution.id:
                self._finish_execution(
                    uow,
                    unit,
                    ExecutionAttemptStatus.ABANDONED,
                    ExecutionRunStatus.ABANDONED,
                )
                return Signal.PAUSED
            plan.release_promotion(unit.execution.id)
            plan.complete_task(unit.goal_id, unit.task_id, result)
            if unit.spec.provider_id and unit.spec.model_id:
                uow.executions.clear_runtime_circuit(
                    unit.spec.runtime_type,
                    unit.spec.provider_id,
                    unit.spec.model_id,
                )
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
