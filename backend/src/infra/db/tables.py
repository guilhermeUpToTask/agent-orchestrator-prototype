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

from sqlalchemy import ForeignKey, Index, Integer, String, Text
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
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    phase: Mapped[str] = mapped_column(String, nullable=False)
    iteration: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    data: Mapped[str] = mapped_column(Text, nullable=False)  # Plan JSON document

    # lease (liveness / crash recovery); epoch seconds UTC
    claimed_by: Mapped[str | None] = mapped_column(String, nullable=True)
    claimed_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lease_expires_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lease_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow_iso)
    updated_at: Mapped[str] = mapped_column(
        String, nullable=False, default=_utcnow_iso, onupdate=_utcnow_iso
    )

    __table_args__ = (Index("ix_plans_claim", "phase", "lease_expires_at"),)


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
# Agent events (fine-grained, best-effort telemetry) — written on their own
# connection, never inside the state transaction.
# ---------------------------------------------------------------------------

class AgentEventTable(Base):
    __tablename__ = "agent_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    plan_id: Mapped[str] = mapped_column(String, nullable=False)
    task_id: Mapped[str] = mapped_column(String, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[str] = mapped_column(String, nullable=False)


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
