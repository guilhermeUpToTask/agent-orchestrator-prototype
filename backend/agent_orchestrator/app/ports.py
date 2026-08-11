"""Application-layer ports + re-exports of the domain ports.

The five execution/side-channel contracts (Clock, AgentEventSink,
WorkspaceHandle/Workspace, AgentRunner, Reasoner) are DOMAIN ports —
they live in agent_orchestrator/domain/ports/ and are re-exported here so use cases,
adapters, and tests keep one import path. What remains defined here is
app-specific: the TaskFailed signal, the transaction machinery
(Outbox, UnitOfWork), and Sandbox (agent_orchestrator/app/sandbox_port.py) — deliberately
an APP port, not a domain one, per ROADMAP item 33: task-attempt OS
confinement is an infra/execution concern the frozen domain must never know
about.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from agent_orchestrator.app.execution_records import ExecutionRecordRepository
from agent_orchestrator.app.acceptance_records import AcceptanceRun, AcceptanceRunRepository
from agent_orchestrator.app.promotion_records import GoalPromotion, GoalPromotionRepository
from agent_orchestrator.app.runtime_failures import RuntimeFailure
from agent_orchestrator.app.sandbox_port import Sandbox, SandboxPolicy, SandboxProbeResult
from agent_orchestrator.domain.events.base import DomainEvent
from agent_orchestrator.domain.ports import (
    AgentEventSink,
    AgentRunner,
    Clock,
    Reasoner,
    Workspace,
    WorkspaceHandle,
)
from agent_orchestrator.domain.ports.reasoner_port import (
    ChatMessage,
    ConversationMode,
    ReasonerReply,
)
from agent_orchestrator.domain.repositories.goal_lease_repo import GoalLeaseRepository
from agent_orchestrator.domain.repositories.planner_repo import PlanRepository
from agent_orchestrator.domain.value_objects.lifecycle import FailureKind

__all__ = [
    "AgentEventSink",
    "AgentRunner",
    "ChatMessage",
    "ChatStore",
    "Clock",
    "ConversationMode",
    "ExecutionRecordRepository",
    "GoalLeaseRepository",
    "GoalPromotion",
    "GoalPromotionRepository",
    "AcceptanceRun",
    "AcceptanceRunRepository",
    "Outbox",
    "PlanningArtifact",
    "PlanningArtifactStore",
    "PriorPlanningAttempts",
    "Reasoner",
    "ReasonerReply",
    "ReasonerUnavailable",
    "Sandbox",
    "SandboxPolicy",
    "SandboxProbeResult",
    "TaskFailed",
    "UnitOfWork",
    "Workspace",
    "WorkspaceHandle",
    "VerificationExecutor",
    "CommandExecution",
    "MainRepositoryWorkspace",
    "PriorAttemptFeedback",
    "PriorAttemptRejection",
    "RepositoryReader",
    "RepositoryOrientation",
    "RepositorySearchHit",
]


@dataclass(frozen=True)
class CommandExecution:
    command: str
    exit_code: int
    started_at: datetime
    finished_at: datetime
    bounded_output_ref: str


@dataclass(frozen=True)
class RepositorySearchHit:
    path: str
    line: int
    text: str


@dataclass(frozen=True)
class RepositoryOrientation:
    """The answer to "what am I planning against?", in one cheap call."""

    default_branch: str
    top_level_entries: tuple[str, ...]
    test_directories: tuple[str, ...]
    detected_test_command: str | None
    config_files: tuple[str, ...]


@runtime_checkable
class RepositoryReader(Protocol):
    """READ-ONLY sight of a project's repository, for the planning reasoner.

    Contracts name exact paths and exact verification commands, and until this
    existed the planner wrote both blind: `read_repository_context` returned a
    hardcoded `{"availability": "adapter_context_only"}`. Observed live, the
    model invented `pytest -q tests/test_greet.py` for a repository whose file
    is `tests/test_greeter.py`, and the resulting frozen contract could not be
    satisfied by any agent.

    Every method is keyed by `project_id`, never by a filesystem path — the
    reasoner must not learn that paths outside the repository exist. Every
    method is bounded: an unbounded read costs TOKEN_LIMIT, which is terminal,
    so a careless call would be worse than no sight at all.

    Implementations read a committed ref, NOT the working tree: during
    enrichment the working tree may hold a concurrent worker's worktree state,
    while the default branch is the stable truth a contract is written against.
    """

    def orientation(self, project_id: str) -> RepositoryOrientation: ...

    def list_paths(
        self, project_id: str, *, prefix: str = "", max_entries: int = 200
    ) -> list[str]: ...

    def read_file(self, project_id: str, path: str, *, max_bytes: int = 20_000) -> str: ...

    def search(
        self,
        project_id: str,
        pattern: str,
        *,
        path_prefix: str = "",
        max_hits: int = 50,
    ) -> list[RepositorySearchHit]: ...

    def exists(self, project_id: str, path: str) -> bool: ...


@dataclass(frozen=True)
class PriorAttemptRejection:
    """Why the orchestrator rejected this task's previous candidate."""

    attempt_number: int
    reasons: tuple[str, ...]


@runtime_checkable
class PriorAttemptFeedback(Protocol):
    """The previous attempt's rejection, for the next attempt's prompt.

    The `AgentRunner` domain port carries no context channel and widening it
    would be a domain change, so the runner reads this instead — the same
    constructor-injection seam the reasoner uses for repository sight. The data
    is already persisted (`ExecutionAttempt.safe_message`); nothing new is
    written, it was simply never read back.

    Implementations return only agent-repairable rejections (see
    `agent_orchestrator/app/agent_feedback.py`): a provider rate limit or a promotion race says
    nothing an agent could act on.
    """

    def last_rejection(
        self, plan_id: str, goal_id: str, task_id: str, *, task_revision: int
    ) -> PriorAttemptRejection | None: ...


@runtime_checkable
class MainRepositoryWorkspace(Protocol):
    """Optional workspace capability for guarding the project repository."""

    async def main_repo_status(self) -> set[str]: ...


@runtime_checkable
class VerificationExecutor(Protocol):
    async def changed_paths(
        self,
        workspace_path: str,
        base_ref: str | None = None,
    ) -> list[str]: ...
    async def run(
        self,
        workspace_path: str,
        commands: list[str],
    ) -> list[CommandExecution]: ...


class TaskFailed(Exception):
    """Raised by an AgentRunner when a task run fails. Carries a human-readable
    `reason` plus a typed `kind` (the shared failure taxonomy) that the domain
    RetryPolicy classifies (retryable vs terminal)."""

    def __init__(
        self,
        reason: str,
        kind: FailureKind | None = None,
        *,
        failure: RuntimeFailure | None = None,
    ) -> None:
        resolved_kind = failure.kind if failure is not None else kind
        if failure is None:
            failure = RuntimeFailure(
                kind=resolved_kind or FailureKind.TOOL_ERROR,
                safe_message=reason[:500],
                retryable=(resolved_kind not in {FailureKind.AUTH_ERROR, FailureKind.TOKEN_LIMIT}),
            )
        self.failure = failure
        self.reason = failure.safe_message
        self.kind = failure.kind
        super().__init__(self.reason)


class ReasonerUnavailable(Exception):
    """Raised by a Reasoner when it cannot produce a usable turn/artifact — the
    planning-phase analog of TaskFailed. `transient` marks a failure worth
    retrying (rate limit, timeout, upstream blip) versus a permanent config
    error (model lacks tool use, bad key). The PlanningHandler catches this to
    arm the plan-level backoff gate or fail the plan.

    Kept app-side (like TaskFailed) so the handler stays free of infra imports;
    the infra ReasonerError subclasses it, so the concrete adapter's raise
    crosses the boundary as this type without app ever importing infra."""

    def __init__(
        self,
        reason: str,
        *,
        transient: bool = False,
        kind: FailureKind | None = None,
        retry_after_seconds: float | None = None,
        partial_artifact: dict[str, object] | None = None,
        rejection_reasons: tuple[str, ...] = (),
        input_fingerprint: str | None = None,
        turns_used: int | None = None,
    ) -> None:
        self.reason = reason
        self.transient = transient
        # What the session had produced when it died, so the RETRY can start from
        # it instead of from nothing. The reasoner reads and never persists (the
        # domain port says so, and that is load-bearing), so it attaches the work
        # to the failure and the handler decides whether to record it.
        self.partial_artifact = partial_artifact
        self.rejection_reasons = rejection_reasons
        # What the attempt was derived from, so a replay can be discarded when the
        # inputs have moved on (an edit, a replan, a catalog change).
        self.input_fingerprint = input_fingerprint
        # Turns this session burned. An attempt that produced nothing still
        # proves the goal is expensive, which is what earns the retry more room.
        self.turns_used = turns_used
        # `transient` alone cannot decide terminality: it lumps a provider rate
        # limit together with "this model cannot do tool calls", and those need
        # opposite treatment -- the first should be waited out indefinitely, the
        # second retried a few times and then escalated, because no amount of
        # waiting will make an incapable model succeed. `kind` is the same
        # FailureKind taxonomy the CLI runners produce, so planning and execution
        # classify capacity identically.
        self.kind = kind
        # Provider-supplied Retry-After, honored as a floor on the backoff.
        self.retry_after_seconds = retry_after_seconds
        super().__init__(reason)


@runtime_checkable
class ChatStore(Protocol):
    """Per-plan conversation history (DISCOVERY / REPLANNING). Writes run
    OUTSIDE the plan UnitOfWork on their own short transactions: a lost display
    reply never loses plan state, and a state rollback never erases what the
    user actually said."""

    def append(self, plan_id: str, message: ChatMessage) -> None: ...
    def list(self, plan_id: str) -> list[ChatMessage]: ...


@dataclass(frozen=True)
class PlanningArtifact:
    """One planning attempt: what was submitted, and why it was refused.

    `input_fingerprint` is what the attempt was derived from. A replay whose
    fingerprint no longer matches the current inputs is discarded rather than
    shown to the model: after an edit, a replan, or a capability-catalog change
    the prior attempt is not merely unhelpful, it describes work the model is no
    longer being asked to do.
    """

    plan_id: str
    purpose: str
    sequence: int
    input_fingerprint: str
    outcome: str  # 'rejected' | 'abandoned' | 'committed'
    created_at: datetime
    goal_id: str | None = None
    operation_id: str | None = None
    payload: dict[str, object] | None = None
    rejection_reasons: tuple[str, ...] = ()
    turns_used: int | None = None


@runtime_checkable
class PriorPlanningAttempts(Protocol):
    """The READ half of `PlanningArtifactStore`, for the reasoner.

    Narrow on purpose: the Reasoner port promises it reads and never persists,
    so handing the adapter something with `append` would invite exactly the
    violation that promise exists to prevent.
    """

    def latest(
        self, plan_id: str, purpose: str, *, goal_id: str | None = None, limit: int = 5
    ) -> list[PlanningArtifact]: ...


@runtime_checkable
class PlanningArtifactStore(Protocol):
    """Planning attempts that survive the transaction that failed.

    Same rule as `ChatStore`, for the same reason: writes run OUTSIDE the plan
    UnitOfWork on their own short transactions. An artifact recorded so a RETRY
    can learn from it must not roll back with the transaction it exists to
    outlive, and it must never be able to roll plan state back either.

    Before this, a rejected submission lived only in the reasoner's in-process
    message list; when the turn budget ran out that list was discarded and the
    retry rebuilt its prompt from scratch, reproducing the same mistake until the
    attempt budget opened a block.
    """

    def append(self, artifact: PlanningArtifact) -> None: ...

    def latest(
        self, plan_id: str, purpose: str, *, goal_id: str | None = None, limit: int = 5
    ) -> list[PlanningArtifact]: ...

    def latest_across_goals(
        self, plan_id: str, purpose: str, *, limit: int = 5
    ) -> list[PlanningArtifact]:
        """Every goal's artifacts for `purpose`, newest first.

        `latest(goal_id=None)` does NOT mean this. It matches `goal_id IS NULL`
        — the plan-wide artifacts — which is correct for a plan-wide purpose and
        returns nothing for a goal-scoped one. There was no way to ask the
        question this answers, which is why a goal-scoped purpose looked like it
        had never been written (Phase 10B, `verification_baseline`).
        """
        ...

    def clear(self, plan_id: str, purpose: str, *, goal_id: str | None = None) -> None: ...


@runtime_checkable
class Outbox(Protocol):
    """Coarse domain events, added INSIDE the state transaction (transactional
    outbox). The UnitOfWork commits state + outbox atomically."""

    def add(self, event: DomainEvent) -> None: ...


@runtime_checkable
class UnitOfWork(Protocol):
    """Transaction boundary. Owns Plan, goal-lease, execution-ledger, and Outbox
    repositories; entering starts a transaction and exiting commits (or rolls back
    on exception). This is how state + execution identity + outbox become atomic.

    Repository attributes are read-only properties on the protocol so concrete
    implementations' narrower attribute types remain assignable (covariance)."""

    @property
    def plans(self) -> PlanRepository: ...
    @property
    def outbox(self) -> Outbox: ...
    @property
    def executions(self) -> ExecutionRecordRepository: ...
    @property
    def goal_leases(self) -> GoalLeaseRepository: ...
    @property
    def promotions(self) -> GoalPromotionRepository: ...

    @property
    def acceptance_runs(self) -> AcceptanceRunRepository:
        """The advisory cycle-acceptance ledger (P8.2)."""
        ...

    def __enter__(self) -> "UnitOfWork": ...
    def __exit__(self, *exc: object) -> None: ...
