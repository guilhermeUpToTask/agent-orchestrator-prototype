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


class TaskFailedEvent(DomainEvent):
    goal_id: str
    task_id: str
    reason: str


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


class AgentFellBackToDefault(DomainEvent):
    """Surfaces a capability-coverage hole: a task matched no agent and used the
    default. Telemetry signal that the agent catalog is missing a capability."""

    task_id: str
    required_capabilities: list[str]
