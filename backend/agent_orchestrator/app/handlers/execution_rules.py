"""Pure decisions the execution handler makes — no `self`, no I/O, no state.

Extracted from `ExecutionHandler` (P8.7 task 3, step 1), which measured **2,133
lines across 44 methods** spanning roughly eight responsibilities. This is the
lowest-risk slice: every function here was already `@staticmethod` or took
`self` without using it, so moving them cannot change behaviour — only make the
remaining file readable enough to split safely.

Nothing here reaches a repository, a clock or a runtime. `_attempts_against_budget`
is the one exception in appearance only: it reads the attempt ledger through a
UnitOfWork passed in as an argument, so it stays a function OF its inputs.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from agent_orchestrator.app.execution_records import ExecutionAttempt
from agent_orchestrator.app.ports import CommandExecution, TaskFailed, UnitOfWork
from agent_orchestrator.app.provider_capacity import capacity_backoff_seconds
from agent_orchestrator.app.runtime_failures import RuntimeFailure
from agent_orchestrator.app.verification import test_author_path_allowed
from agent_orchestrator.domain.aggregates.planner_orchestrator import Plan
from agent_orchestrator.domain.entities.agent_spec import AgentSpec
from agent_orchestrator.domain.entities.execution_contracts import VerificationStrategy
from agent_orchestrator.domain.entities.task import Task
from agent_orchestrator.domain.policies.retry_policies import RetryPolicy
from agent_orchestrator.domain.services.lookups import find_goal, find_task
from agent_orchestrator.domain.value_objects.lifecycle import FailureKind


@dataclass
class Unit:
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


def run_role_for(plan: Plan, task: Task) -> str:
    """Which stage runs next for this task: author the checks, or implement.

    One definition, because `ExecutionHandler._start_unit` and
    `AgentAdmission.resolve_spec` must agree — they each had their own copy of
    this expression, and a divergence would resolve an agent for one role while
    running the other. It lives here rather than in either caller precisely
    because they now sit in different modules.
    """
    if plan.active_cycle is None or task.contract is None:
        return "implementer"
    if task.test_bundle is not None and task.test_bundle.validates(task.id, task.revision):
        return "implementer"
    return "test_author"


def orchestration_failure(reason: str) -> RuntimeFailure:
    """A Class C failure: an orchestration race or missing infrastructure.

    Un-freeze #17 made VERIFICATION_ERROR retryable so a rejected CANDIDATE gets
    a second attempt. These are not candidates — a superseded cycle, evidence
    that moved during promotion, an absent verifier — and retrying only re-races
    them. `RuntimeFailure.retryable` is an independent veto in the retry
    condition, so the split stays structural rather than keyed off message text.
    """
    return RuntimeFailure(
        kind=FailureKind.VERIFICATION_ERROR,
        safe_message=reason,
        retryable=False,
    )


def main_repo_failure(stray_paths: list[str]) -> TaskFailed:
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


def raise_on_infrastructure_exit(outcomes: list[CommandExecution]) -> None:
    """Exit 126/127 means the command could not run at all — never a test verdict."""
    failure = next((item for item in outcomes if item.exit_code in {126, 127}), None)
    if failure is not None:
        raise TaskFailed(
            f"verification command {failure.command!r} failed with exit code "
            f"{failure.exit_code} (infrastructure failure)",
            FailureKind.TOOL_ERROR,
        )


def author_path_allowed(path: str, strategy: VerificationStrategy) -> bool:
    # One definition, shared with the reasoner's submission-time check so a
    # contract cannot freeze a strategy its own scope makes unsatisfiable.
    return test_author_path_allowed(path, strategy)


def unit_task(plan: Plan, unit: Unit) -> Task:
    goals = plan.goals
    if unit.cycle_id is not None:
        cycle = next((item for item in plan.cycles if item.id == unit.cycle_id), None)
        if cycle is None:
            raise TaskFailed(
                f"captured cycle '{unit.cycle_id}' no longer exists",
                # Class C: the cycle was superseded mid-run, and retrying
                # re-races it. Un-freeze #17 made VERIFICATION_ERROR retryable
                # for candidate rejections; this is not one, so it keeps the
                # independent `retryable` veto.
                failure=orchestration_failure(
                    f"captured cycle '{unit.cycle_id}' no longer exists"
                ),
            )
        goals = cycle.goals
    return find_task(find_goal(goals, unit.goal_id), unit.task_id)


def jitter_unit(attempt_id: str) -> float:
    digest = hashlib.sha256(attempt_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(2**64 - 1)


def retry_delay_seconds(unit: Unit, kind: FailureKind | None, failure: RuntimeFailure) -> float:
    """How long before this failed attempt may run again. 0 means immediately.

    Three rules, in order of who knows best: the plan's own backoff curve, then
    an explicit `Retry-After` from the provider (never shorten what upstream
    asked for), then a floor for a daily quota that arrived without one — a
    quota resets on a clock, and retrying it on a seconds-scale curve just burns
    attempts against a door that opens tomorrow.
    """
    delay = capacity_backoff_seconds(
        unit.retry_policy,
        unit.policy_attempt + 1,
        jitter_unit=jitter_unit(unit.execution.id),
        kind=kind,
        # A concurrency refusal does not escalate like an exhausted
        # allowance; every other scope keeps the patient curve.
        limit_scope=failure.limit_scope,
    )
    if failure.retry_after_seconds is not None:
        delay = max(delay, failure.retry_after_seconds)
    if (
        failure.limit_scope is not None
        and failure.limit_scope.value == "daily_quota"
        and failure.retry_after_seconds is None
    ):
        delay = max(delay, 3_600.0)
    return delay


def attempts_against_budget(
    plan_id: str, unit: Unit, kind: FailureKind | None, uow: UnitOfWork
) -> int:
    """How many attempts this failure's budget has actually consumed.

    `cycle_attempt` counts EVERY attempt, including ones that never reached the
    work: a provider capacity failure means there was no room upstream, not that
    the agent produced something wrong. For a kind bounded by a ceiling that
    distinction decides the outcome — observed live, a task waited out three rate
    limits and then its FIRST real candidate rejection blocked the goal, because
    the ceiling of 2 had already been spent by failures of an unrelated kind.

    Kinds with a ceiling are therefore counted per KIND. Everything else keeps
    the whole-task counter, which is what a general attempt budget means.
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
