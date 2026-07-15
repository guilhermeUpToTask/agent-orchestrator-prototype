"""Coarse domain events — state transitions. Written to the outbox in the SAME
transaction as the state change (transactional outbox), so state and event can
never diverge. A relay (deferred) ships them to Redis later; consumers dedup on
event_id."""

from __future__ import annotations

from src.domain.events.base import DomainEvent


class PhaseAdvanced(DomainEvent):
    from_phase: str
    to_phase: str


class TaskStarted(DomainEvent):
    goal_id: str
    task_id: str
    attempt: int


class TaskCompleted(DomainEvent):
    goal_id: str
    task_id: str


class TaskRequeued(DomainEvent):
    goal_id: str
    task_id: str
    attempt: int
    reason: str
    kind: str | None = None  # FailureKind value — rate_limit visibility


class TaskFailedEvent(DomainEvent):
    goal_id: str
    task_id: str
    reason: str
    kind: str | None = None  # FailureKind value — rate_limit visibility


class TaskAbandoned(DomainEvent):
    """Tolerant finalize closed an in-flight task because its iteration was
    abandoned by a replan (terminal-skip, never requeued)."""

    goal_id: str
    task_id: str
    reason: str


class ReplanRequested(DomainEvent):
    """A conversational re-plan was requested (from REVIEW or mid-RUNNING chat).
    Pending work of the current iteration was skipped."""

    from_phase: str


class GoalCompleted(DomainEvent):
    goal_id: str


class GoalFailedEvent(DomainEvent):
    goal_id: str


# No extra fields: the base (plan_id + event_id + occurred_at) fully identifies it;
# the distinct type is the signal.
class PlanCompleted(DomainEvent):
    pass


class PlanFailed(DomainEvent):
    reason: str


class PauseRequested(DomainEvent):
    """A graceful pause is waiting for the current atomic action to finalize."""

    reason: str | None = None


class PlanBlocked(DomainEvent):
    block_id: str
    stage: str
    goal_id: str | None = None
    task_id: str | None = None
    task_revision: int | None = None
    run_id: str | None = None


class BlockResolved(DomainEvent):
    block_id: str
    resolution: str


class PlanPaused(DomainEvent):
    """The plan's pause gate was armed. auto=True means the system paused itself
    — a task exhausted its retry budget or failed non-retryably — and needs
    human attention (edit while paused, then resume); auto=False is a human
    pause command."""

    reason: str | None = None
    auto: bool = False


class PlanResumed(DomainEvent):
    """Human resume: the pause gate cleared and FAILED tasks were requeued with
    a fresh attempt budget (the manual retry, decision #17)."""

    retried_task_ids: list[str] = []


class IntentProposed(DomainEvent):
    proposal_id: str
    revision: int


class IntentApproved(DomainEvent):
    proposal_id: str
    revision: int


class CycleDrafted(DomainEvent):
    draft_id: str
    revision: int


class CycleVerified(DomainEvent):
    cycle_id: str
    evidence_refs: list[str]


class CycleActivated(DomainEvent):
    cycle_id: str
    draft_id: str


class ReviewGateOpened(DomainEvent):
    gate_id: str
    subject_type: str
    subject_id: str
    subject_revision: int


class OutputDispositionRecorded(DomainEvent):
    cycle_id: str
    disposition: str
    output_reference: str | None = None


class TestBundleFrozen(DomainEvent):
    goal_id: str
    task_id: str
    task_revision: int
    test_commit_sha: str


class TaskVerificationAccepted(DomainEvent):
    goal_id: str
    task_id: str
    task_revision: int
    evidence_refs: list[str]


class TaskVerificationRejected(DomainEvent):
    goal_id: str
    task_id: str
    task_revision: int
    reasons: list[str]


class TaskRetried(DomainEvent):
    """A human reset retry-policy budget for one selected task."""

    goal_id: str
    task_id: str
    retry_cycle: int
    next_attempt_number: int


class ReasonerFailed(DomainEvent):
    """The planning LLM failed in a worker-driven planning phase (ARCHITECTURE/
    ENRICHING). `transient` true = the plan armed its backoff gate and will retry
    (`retry_at` is when); false = the failure was permanent or the retry budget was
    exhausted and the plan moved to FAILED (a PlanFailed follows). This is the
    signal that reaches the frontend — a worker-phase reasoner failure used to be
    invisible outside the worker logs."""

    phase: str
    reason: str
    transient: bool
    retry_at: str | None = None


class AgentFellBackToDefault(DomainEvent):
    """Surfaces a capability-coverage hole: a task matched no agent and used the
    default. Telemetry signal that the agent catalog is missing a capability."""

    task_id: str
    required_capabilities: list[str]
