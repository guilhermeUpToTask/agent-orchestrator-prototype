"""
src/infra/db/tables.py — SQLAlchemy table definitions for the orchestrator DB.

Schema decision (integration Stage 3): the Plan aggregate is stored as ONE JSON
document (``plans.data``) with promoted scalar columns only for what SQL must
predicate on — the claim query (phase + lease columns) and the version CAS.
Goals/tasks live inside the JSON: the CAS is plan-level (every save writes the
whole aggregate), ``PlanFactory.reconstruct(dict)`` is the rehydration hook, and
a fresh JSON parse per ``get`` gives detached-aggregate semantics for free.
There is NO ORM mapping of the domain Plan — repositories do JSON in/out.

Lease times are integer unix epochs (UTC) so the claim SQL compares numerically;
human-facing timestamps are ISO-8601 strings.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Plans — the aggregate document + lease
# ---------------------------------------------------------------------------


class PlanTable(Base):
    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str | None] = mapped_column(String, ForeignKey("projects.id"), nullable=True)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="blocked", server_default="blocked"
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    phase: Mapped[str] = mapped_column(String, nullable=False)
    iteration: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    data: Mapped[str] = mapped_column(Text, nullable=False)  # Plan JSON document

    # lease (liveness / crash recovery); epoch seconds UTC
    claimed_by: Mapped[str | None] = mapped_column(String, nullable=True)
    claimed_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lease_expires_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lease_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Durable plan-level backoff gate (epoch seconds UTC): a worker-driven planning
    # phase that hit a transient reasoner failure is not re-claimed until now passes
    # this. Projected from Plan.planning_retry_not_before; the claim predicate ANDs it.
    retry_not_before: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Human pause gate (un-freeze #3): projected from Plan.paused; the claim
    # predicate skips paused plans, so pause holds durably across crashes.
    paused: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    pause_requested: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow_iso)
    updated_at: Mapped[str] = mapped_column(
        String, nullable=False, default=_utcnow_iso, onupdate=_utcnow_iso
    )

    __table_args__ = (
        Index("ix_plans_claim", "status", "pause_requested", "lease_expires_at"),
        Index(
            "uq_plans_project_id",
            "project_id",
            unique=True,
            sqlite_where=project_id.is_not(None),
        ),
    )


class PlanRequestTable(Base):
    """API-layer create idempotency: request_id -> plan_id."""

    __tablename__ = "plan_requests"

    request_id: Mapped[str] = mapped_column(String, primary_key=True)
    plan_id: Mapped[str] = mapped_column(String, ForeignKey("plans.id"), nullable=False)


# ---------------------------------------------------------------------------
# Transactional outbox (coarse domain events) — written in the SAME transaction
# as the plan state; a relay delivers rows and marks delivered_at. Consumers
# dedup on event_id (delivery is at-least-once).
# ---------------------------------------------------------------------------


class OutboxTable(Base):
    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    plan_id: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)  # event JSON
    occurred_at: Mapped[str] = mapped_column(String, nullable=False)
    delivered_at: Mapped[str | None] = mapped_column(String, nullable=True)


# Partial index: the relay polls only undelivered rows.
Index(
    "ix_outbox_undelivered",
    OutboxTable.id,
    sqlite_where=OutboxTable.delivered_at.is_(None),
)


# ---------------------------------------------------------------------------
# Execution ledger — operational run/attempt identity. These rows are written
# in the SAME UnitOfWork transaction as the corresponding task transition and
# domain outbox event, but they are not domain aggregates or telemetry exports.
# ---------------------------------------------------------------------------


class ExecutionRunTable(Base):
    __tablename__ = "execution_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    plan_id: Mapped[str] = mapped_column(
        String, ForeignKey("plans.id", ondelete="CASCADE"), nullable=False
    )
    goal_id: Mapped[str] = mapped_column(String, nullable=False)
    task_id: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[str] = mapped_column(String, nullable=False)
    completed_at: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'retrying', 'succeeded', 'failed', 'abandoned')",
            name="ck_execution_runs_status",
        ),
        Index("ix_execution_runs_plan_task", "plan_id", "goal_id", "task_id", "id"),
    )


# Sequential-per-plan execution means only one logical active run may exist for
# a task. The partial unique index preserves history while making retry/reopen
# identity races fail transactionally instead of producing parallel active runs.
Index(
    "uq_execution_runs_active_task",
    ExecutionRunTable.plan_id,
    ExecutionRunTable.goal_id,
    ExecutionRunTable.task_id,
    unique=True,
    sqlite_where=ExecutionRunTable.status.in_(("running", "retrying")),
)


class ExecutionAttemptTable(Base):
    __tablename__ = "execution_attempts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String, ForeignKey("execution_runs.id", ondelete="CASCADE"), nullable=False
    )
    plan_id: Mapped[str] = mapped_column(
        String, ForeignKey("plans.id", ondelete="CASCADE"), nullable=False
    )
    goal_id: Mapped[str] = mapped_column(String, nullable=False)
    task_id: Mapped[str] = mapped_column(String, nullable=False)
    # Monotonic across the task lifetime, unlike Task.attempt which resets on a
    # human retry. This number is safe for workspace/branch naming.
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    # Snapshot of the domain retry counter for policy/debug correlation.
    task_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[str] = mapped_column(String, nullable=False)
    completed_at: Mapped[str | None] = mapped_column(String, nullable=True)
    last_liveness_at: Mapped[str | None] = mapped_column(String, nullable=True)
    timeout_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    runtime: Mapped[str | None] = mapped_column(String, nullable=True)
    provider_id: Mapped[str | None] = mapped_column(String, nullable=True)
    model_id: Mapped[str | None] = mapped_column(String, nullable=True)
    failure_kind: Mapped[str | None] = mapped_column(String, nullable=True)
    provider_code: Mapped[str | None] = mapped_column(String, nullable=True)
    retryable: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retry_at: Mapped[str | None] = mapped_column(String, nullable=True)
    limit_scope: Mapped[str | None] = mapped_column(String, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    safe_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    stdout_tail: Mapped[str | None] = mapped_column(Text, nullable=True)
    stderr_tail: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'abandoned')",
            name="ck_execution_attempts_status",
        ),
        UniqueConstraint(
            "plan_id",
            "goal_id",
            "task_id",
            "number",
            name="uq_execution_attempts_task_number",
        ),
        Index("ix_execution_attempts_open", "status", "started_at"),
        Index("ix_execution_attempts_run", "run_id", "number"),
    )


class PlanningOperationTable(Base):
    __tablename__ = "planning_operations"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    plan_id: Mapped[str] = mapped_column(
        String, ForeignKey("plans.id", ondelete="CASCADE"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(String, nullable=False)
    target_goal_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[str | None] = mapped_column(String, nullable=True)
    completed_at: Mapped[str | None] = mapped_column(String, nullable=True)
    last_liveness_at: Mapped[str | None] = mapped_column(String, nullable=True)
    model_request_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    tool_turn_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    runtime: Mapped[str | None] = mapped_column(String, nullable=True)
    provider_id: Mapped[str | None] = mapped_column(String, nullable=True)
    model_id: Mapped[str | None] = mapped_column(String, nullable=True)
    failure_kind: Mapped[str | None] = mapped_column(String, nullable=True)
    retry_at: Mapped[str | None] = mapped_column(String, nullable=True)
    safe_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'started', 'waiting_for_user', 'committed', "
            "'failed', 'backing_off')",
            name="ck_planning_operations_status",
        ),
        Index("ix_planning_operations_plan", "plan_id", "created_at", "id"),
        Index(
            "ix_planning_operations_active",
            "plan_id",
            "purpose",
            "target_goal_id",
            "status",
        ),
    )


class RuntimeCircuitTable(Base):
    __tablename__ = "runtime_circuits"

    runtime: Mapped[str] = mapped_column(String, primary_key=True)
    provider_id: Mapped[str] = mapped_column(String, primary_key=True)
    model_id: Mapped[str] = mapped_column(String, primary_key=True)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False)
    opened_at: Mapped[str] = mapped_column(String, nullable=False)
    retry_at: Mapped[str] = mapped_column(String, nullable=False)
    last_failure_kind: Mapped[str] = mapped_column(String, nullable=False)
    safe_message: Mapped[str] = mapped_column(Text, nullable=False)
    manual_intervention: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    __table_args__ = (Index("ix_runtime_circuits_retry", "retry_at"),)


# ---------------------------------------------------------------------------
# Agent events (fine-grained, best-effort telemetry) — written on their own
# connection, never inside the state transaction.
# ---------------------------------------------------------------------------


class AgentEventTable(Base):
    __tablename__ = "agent_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    plan_id: Mapped[str] = mapped_column(String, nullable=False)
    goal_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # NULL task_id = a plan-scoped telemetry row (e.g. the reasoner's llm.call);
    # task-scoped rows carry the task id as before.
    task_id: Mapped[str | None] = mapped_column(String, nullable=True)
    run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    attempt_id: Mapped[str | None] = mapped_column(String, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    # Canonical operational metadata. Legacy writers/readers keep using type,
    # attempt, and seq while new observations use the explicit fields.
    observation_kind: Mapped[str | None] = mapped_column(String, nullable=True)
    source: Mapped[str] = mapped_column(
        String, nullable=False, default="legacy", server_default="legacy"
    )
    quality: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="legacy_unknown",
        server_default="legacy_unknown",
    )
    schema_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    source_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[str] = mapped_column(String, nullable=False)
    recorded_at: Mapped[str | None] = mapped_column(String, nullable=True)

    # The per-plan / per-task history read path (GET /plans/{id}/agent-events).
    __table_args__ = (
        Index("ix_agent_events_plan_task", "plan_id", "task_id", "id"),
        Index("ix_agent_events_run", "run_id", "id"),
        Index("ix_agent_events_attempt_id", "attempt_id", "id"),
        Index("ix_agent_events_kind", "observation_kind", "id"),
    )


# ---------------------------------------------------------------------------
# Plan chat (DISCOVERY / REPLANNING conversation history) — written on its own
# short transactions, never inside the plan UnitOfWork: a lost display reply
# must never lose plan state (and vice versa).
# ---------------------------------------------------------------------------


class PlanChatMessageTable(Base):
    __tablename__ = "plan_chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[str] = mapped_column(String, ForeignKey("plans.id"), nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)  # 'user' | 'assistant'
    content: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow_iso)

    __table_args__ = (Index("ix_plan_chat_messages_plan", "plan_id", "id"),)


# ---------------------------------------------------------------------------
# Reference data (user-managed catalogs). AgentSpec embeds its capabilities and
# ModelProvider embeds its models in the domain; here they normalize into join/
# child tables so the integrity rules (delete-guard, cascade-down/guard-up)
# are enforced by the schema.
# ---------------------------------------------------------------------------


class CapabilityTable(Base):
    __tablename__ = "capabilities"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tools: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON list


class AgentTable(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    model_role: Mapped[str] = mapped_column(String, nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False)
    default_retry: Mapped[str] = mapped_column(Text, nullable=False)  # RetryPolicy JSON
    is_default: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # runtime resolution: which CLI runtime + catalog provider/model the agent
    # runs on. Deliberately NOT FK-constrained (SQLite can't add FKs to an
    # existing table): the dangling-ref net applies — the runner factory
    # validates the wiring and /api/runner/status flags broken bindings.
    runtime_type: Mapped[str] = mapped_column(String, nullable=False, default="pi")
    provider_id: Mapped[str | None] = mapped_column(String, nullable=True)
    model_id: Mapped[str | None] = mapped_column(String, nullable=True)


class AgentCapabilityTable(Base):
    __tablename__ = "agent_capabilities"

    agent_id: Mapped[str] = mapped_column(
        String, ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True
    )
    capability_id: Mapped[str] = mapped_column(
        String, ForeignKey("capabilities.id", ondelete="RESTRICT"), primary_key=True
    )


class ProviderTable(Base):
    __tablename__ = "providers"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    base_url: Mapped[str] = mapped_column(String, nullable=False)
    # secret URI into the secrets table — NEVER a plaintext key
    api_key_ref: Mapped[str] = mapped_column(String, nullable=False)


class ModelTable(Base):
    __tablename__ = "models"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    provider_id: Mapped[str] = mapped_column(
        String, ForeignKey("providers.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)


class ProjectTable(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    repo_url: Mapped[str | None] = mapped_column(String, nullable=True)


class ConfigTable(Base):
    """Two-tier config: scope 'orchestrator' for machine settings, a project id
    for per-project settings."""

    __tablename__ = "config"

    scope: Mapped[str] = mapped_column(String, primary_key=True)
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


# ---------------------------------------------------------------------------
# Secrets (envelope encryption) — carried over from the old backend; stores the
# ciphertext + wrapped data key only, never plaintext.
# ---------------------------------------------------------------------------


class SecretTable(Base):
    __tablename__ = "secrets"

    uri: Mapped[str] = mapped_column(String, primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    wrapped_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow_iso)
    updated_at: Mapped[str] = mapped_column(
        String, nullable=False, default=_utcnow_iso, onupdate=_utcnow_iso
    )
