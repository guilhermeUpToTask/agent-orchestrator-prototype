"""
src/infra/db/reference_repos.py — SQLite reference-data repositories.

Implements the domain repository ports for the user-managed catalogs (agents,
capabilities, providers, models, projects) plus the two-tier config store.
Each operation runs in its own short transaction (run_in_session) — reference
CRUD is not part of the plan UnitOfWork.

Integrity rules (see src/domain/errors/README.md):
- delete-guard      -> ReferencedEntityInUseError while something active refers
                       to the entity.
- cascade-down /    -> deleting a provider cascades to its models (FK CASCADE),
  guard-up             but is refused while one of those models is in use.
- dangling-ref net  -> get() raises the typed NotFound so execution fails clean.

Prototype-grade note: "referenced by a non-terminal plan" is checked with a
substring scan over the plan JSON documents (LIKE) — coarse but fail-safe
(false positives block a delete; false negatives are impossible for the exact
quoted-id patterns used).
"""

from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from src.domain.entities.agent_spec import AgentSpec
from src.domain.entities.capability import Capability
from src.domain.entities.ia_model import IAModel
from src.domain.entities.model_provider import ModelProvider
from src.domain.entities.project_definition import ProjectDefinition
from src.domain.errors.agent_errors import AgentNotFoundError, NoDefaultAgentError
from src.domain.errors.config_errors import (
    CapabilityNotFoundError,
    EntityAlreadyExistsError,
    ModelNotFoundError,
    ModelProviderNotFoundError,
    ReferencedEntityInUseError,
)
from src.domain.policies.retry_policies import RetryPolicy
from src.infra.db._session import run_in_session
from src.infra.db.tables import (
    AgentCapabilityTable,
    AgentTable,
    CapabilityTable,
    ConfigTable,
    ModelTable,
    ProjectTable,
    ProviderTable,
)

_NONTERMINAL_PLAN_REF_SQL = text(
    """
    SELECT COUNT(*) FROM plans
    WHERE phase NOT IN ('done', 'failed') AND data LIKE :pattern
    """
)


def _referenced_by_active_plan(session: Session, quoted_fragment: str) -> bool:
    """Prototype-grade reference scan over non-terminal plan JSON documents."""
    count = session.execute(
        _NONTERMINAL_PLAN_REF_SQL, {"pattern": f"%{quoted_fragment}%"}
    ).scalar_one()
    return int(count) > 0


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


def _capability_from_row(row: CapabilityTable) -> Capability:
    return Capability(
        id=row.id,
        name=row.name,
        description=row.description,
        tools=json.loads(row.tools),
    )


class SqliteCapabilityRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def get(self, capability_id: str) -> Capability:
        with self._sf() as s:
            row = s.get(CapabilityTable, capability_id)
            if row is None:
                raise CapabilityNotFoundError(capability_id)
            return _capability_from_row(row)

    def add(self, capability: Capability) -> None:
        def _op(s: Session) -> None:
            if s.get(CapabilityTable, capability.id) is not None:
                raise EntityAlreadyExistsError("Capability", capability.id)
            s.add(
                CapabilityTable(
                    id=capability.id,
                    name=capability.name,
                    description=capability.description,
                    tools=json.dumps(capability.tools),
                )
            )

        run_in_session(self._sf, _op)

    def update(self, capability: Capability) -> None:
        def _op(s: Session) -> None:
            row = s.get(CapabilityTable, capability.id)
            if row is None:
                raise CapabilityNotFoundError(capability.id)
            row.name = capability.name
            row.description = capability.description
            row.tools = json.dumps(capability.tools)

        run_in_session(self._sf, _op)

    def delete(self, capability_id: str) -> None:
        def _op(s: Session) -> None:
            row = s.get(CapabilityTable, capability_id)
            if row is None:
                raise CapabilityNotFoundError(capability_id)
            agent_refs = s.execute(
                text("SELECT agent_id FROM agent_capabilities WHERE capability_id = :cid LIMIT 1"),
                {"cid": capability_id},
            ).one_or_none()
            if agent_refs is not None:
                raise ReferencedEntityInUseError(
                    "Capability", capability_id, f"agent '{agent_refs[0]}'"
                )
            if _referenced_by_active_plan(s, f'"{capability_id}"'):
                raise ReferencedEntityInUseError("Capability", capability_id, "a non-terminal plan")
            s.delete(row)

        run_in_session(self._sf, _op)

    def list(self) -> list[Capability]:
        with self._sf() as s:
            rows = s.query(CapabilityTable).order_by(CapabilityTable.id).all()
            return [_capability_from_row(r) for r in rows]


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


class SqliteAgentRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def _hydrate(self, s: Session, row: AgentTable) -> AgentSpec:
        cap_rows = (
            s.query(CapabilityTable)
            .join(
                AgentCapabilityTable,
                AgentCapabilityTable.capability_id == CapabilityTable.id,
            )
            .filter(AgentCapabilityTable.agent_id == row.id)
            .order_by(CapabilityTable.id)
            .all()
        )
        return AgentSpec(
            id=row.id,
            name=row.name,
            role=row.role,
            model_role=row.model_role,
            instructions=row.instructions,
            capabilities=[_capability_from_row(c) for c in cap_rows],
            default_retry=RetryPolicy.model_validate_json(row.default_retry),
            runtime_type=row.runtime_type,
            provider_id=row.provider_id,
            model_id=row.model_id,
        )

    def _write_capability_links(self, s: Session, agent: AgentSpec) -> None:
        for cap in agent.capabilities:
            if s.get(CapabilityTable, cap.id) is None:
                raise CapabilityNotFoundError(cap.id)
        # flush the pending agent row first: with no ORM relationship() declared,
        # the unit of work does NOT order inserts across mappers by FK, and the
        # join rows would hit the agents FK before the agent exists.
        s.flush()
        s.execute(
            text("DELETE FROM agent_capabilities WHERE agent_id = :aid"),
            {"aid": agent.id},
        )
        for cap in agent.capabilities:
            s.add(AgentCapabilityTable(agent_id=agent.id, capability_id=cap.id))

    def get(self, agent_id: str) -> AgentSpec:
        with self._sf() as s:
            row = s.get(AgentTable, agent_id)
            if row is None:
                raise AgentNotFoundError(agent_id)
            return self._hydrate(s, row)

    def add(self, agent: AgentSpec) -> None:
        def _op(s: Session) -> None:
            if s.get(AgentTable, agent.id) is not None:
                raise EntityAlreadyExistsError("Agent", agent.id)
            s.add(
                AgentTable(
                    id=agent.id,
                    name=agent.name,
                    role=agent.role,
                    model_role=agent.model_role,
                    instructions=agent.instructions,
                    default_retry=agent.default_retry.model_dump_json(),
                    runtime_type=agent.runtime_type,
                    provider_id=agent.provider_id,
                    model_id=agent.model_id,
                )
            )
            self._write_capability_links(s, agent)

        run_in_session(self._sf, _op)

    def update(self, agent: AgentSpec) -> None:
        def _op(s: Session) -> None:
            row = s.get(AgentTable, agent.id)
            if row is None:
                raise AgentNotFoundError(agent.id)
            row.name = agent.name
            row.role = agent.role
            row.model_role = agent.model_role
            row.instructions = agent.instructions
            row.default_retry = agent.default_retry.model_dump_json()
            row.runtime_type = agent.runtime_type
            row.provider_id = agent.provider_id
            row.model_id = agent.model_id
            self._write_capability_links(s, agent)

        run_in_session(self._sf, _op)

    def delete(self, agent_id: str) -> None:
        def _op(s: Session) -> None:
            row = s.get(AgentTable, agent_id)
            if row is None:
                raise AgentNotFoundError(agent_id)
            if _referenced_by_active_plan(s, f'"agent_id":"{agent_id}"'):
                raise ReferencedEntityInUseError("Agent", agent_id, "a non-terminal plan")
            s.delete(row)  # agent_capabilities rows cascade

        run_in_session(self._sf, _op)

    def default_agent_id(self) -> str:
        with self._sf() as s:
            row = s.execute(
                text("SELECT id FROM agents WHERE is_default = 1 LIMIT 1")
            ).one_or_none()
        if row is None:
            raise NoDefaultAgentError()
        return str(row[0])

    def get_default_id(self) -> str | None:
        """Non-raising read of the default-agent marker (API status reads)."""
        with self._sf() as s:
            row = s.execute(
                text("SELECT id FROM agents WHERE is_default = 1 LIMIT 1")
            ).one_or_none()
        return None if row is None else str(row[0])

    def set_default(self, agent_id: str) -> None:
        """Repo-level default-agent marker (not part of AgentSpec)."""

        def _op(s: Session) -> None:
            if s.get(AgentTable, agent_id) is None:
                raise AgentNotFoundError(agent_id)
            s.execute(text("UPDATE agents SET is_default = 0"))
            s.execute(
                text("UPDATE agents SET is_default = 1 WHERE id = :aid"),
                {"aid": agent_id},
            )

        run_in_session(self._sf, _op)

    def list(self) -> list[AgentSpec]:
        with self._sf() as s:
            rows = s.query(AgentTable).order_by(AgentTable.id).all()
            return [self._hydrate(s, r) for r in rows]


# ---------------------------------------------------------------------------
# Models & providers (provider owns its models: cascade down, guard up)
# ---------------------------------------------------------------------------


def _model_from_row(row: ModelTable) -> IAModel:
    return IAModel(id=row.id, provider_id=row.provider_id, name=row.name)


def _guard_model_in_use(s: Session, model_id: str) -> None:
    """Guard-up: a model referenced by config (the model_role tier mapping)
    or bound to an agent's runtime cannot be deleted."""
    ref = s.execute(
        text("SELECT scope, key FROM config WHERE value = :mid LIMIT 1"),
        {"mid": model_id},
    ).one_or_none()
    if ref is not None:
        raise ReferencedEntityInUseError("Model", model_id, f"config '{ref[0]}/{ref[1]}'")
    agent_ref = s.execute(
        text("SELECT id FROM agents WHERE model_id = :mid LIMIT 1"),
        {"mid": model_id},
    ).one_or_none()
    if agent_ref is not None:
        raise ReferencedEntityInUseError("Model", model_id, f"agent '{agent_ref[0]}'")


class SqliteModelRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def get(self, model_id: str) -> IAModel:
        with self._sf() as s:
            row = s.get(ModelTable, model_id)
            if row is None:
                raise ModelNotFoundError(model_id)
            return _model_from_row(row)

    def add(self, model: IAModel) -> None:
        def _op(s: Session) -> None:
            if s.get(ModelTable, model.id) is not None:
                raise EntityAlreadyExistsError("Model", model.id)
            if s.get(ProviderTable, model.provider_id) is None:
                raise ModelProviderNotFoundError(model.provider_id)
            s.add(ModelTable(id=model.id, provider_id=model.provider_id, name=model.name))

        run_in_session(self._sf, _op)

    def update(self, model: IAModel) -> None:
        def _op(s: Session) -> None:
            row = s.get(ModelTable, model.id)
            if row is None:
                raise ModelNotFoundError(model.id)
            row.provider_id = model.provider_id
            row.name = model.name

        run_in_session(self._sf, _op)

    def delete(self, model_id: str) -> None:
        def _op(s: Session) -> None:
            row = s.get(ModelTable, model_id)
            if row is None:
                raise ModelNotFoundError(model_id)
            _guard_model_in_use(s, model_id)
            s.delete(row)

        run_in_session(self._sf, _op)

    def list_by_provider(self, provider_id: str) -> list[IAModel]:
        with self._sf() as s:
            rows = (
                s.query(ModelTable)
                .filter(ModelTable.provider_id == provider_id)
                .order_by(ModelTable.id)
                .all()
            )
            return [_model_from_row(r) for r in rows]

    def list(self) -> list[IAModel]:
        with self._sf() as s:
            rows = s.query(ModelTable).order_by(ModelTable.id).all()
            return [_model_from_row(r) for r in rows]


class SqliteModelProviderRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def _hydrate(self, s: Session, row: ProviderTable) -> ModelProvider:
        model_rows = (
            s.query(ModelTable)
            .filter(ModelTable.provider_id == row.id)
            .order_by(ModelTable.id)
            .all()
        )
        return ModelProvider(
            id=row.id,
            name=row.name,
            base_url=row.base_url,
            api_key_ref=row.api_key_ref,
            models=[_model_from_row(m) for m in model_rows],
        )

    def _reconcile_models(self, s: Session, provider: ModelProvider) -> None:
        """The entity owns its models: make the model rows match provider.models."""
        s.flush()  # provider row must exist before model rows hit its FK (no ORM
        # relationship() -> the unit of work won't order these inserts itself)
        existing = {
            r.id: r for r in s.query(ModelTable).filter(ModelTable.provider_id == provider.id)
        }
        wanted = {m.id: m for m in provider.models}
        for gone_id in existing.keys() - wanted.keys():
            _guard_model_in_use(s, gone_id)
            s.delete(existing[gone_id])
        for model in provider.models:
            row = existing.get(model.id)
            if row is None:
                s.add(ModelTable(id=model.id, provider_id=provider.id, name=model.name))
            else:
                row.name = model.name

    def get(self, provider_id: str) -> ModelProvider:
        with self._sf() as s:
            row = s.get(ProviderTable, provider_id)
            if row is None:
                raise ModelProviderNotFoundError(provider_id)
            return self._hydrate(s, row)

    def add(self, provider: ModelProvider) -> None:
        def _op(s: Session) -> None:
            if s.get(ProviderTable, provider.id) is not None:
                raise EntityAlreadyExistsError("Provider", provider.id)
            s.add(
                ProviderTable(
                    id=provider.id,
                    name=provider.name,
                    base_url=provider.base_url,
                    api_key_ref=provider.api_key_ref,
                )
            )
            self._reconcile_models(s, provider)

        run_in_session(self._sf, _op)

    def update(self, provider: ModelProvider) -> None:
        def _op(s: Session) -> None:
            row = s.get(ProviderTable, provider.id)
            if row is None:
                raise ModelProviderNotFoundError(provider.id)
            row.name = provider.name
            row.base_url = provider.base_url
            row.api_key_ref = provider.api_key_ref
            self._reconcile_models(s, provider)

        run_in_session(self._sf, _op)

    def delete(self, provider_id: str) -> None:
        def _op(s: Session) -> None:
            row = s.get(ProviderTable, provider_id)
            if row is None:
                raise ModelProviderNotFoundError(provider_id)
            # guard UP before the cascade DOWN
            agent_ref = s.execute(
                text("SELECT id FROM agents WHERE provider_id = :pid LIMIT 1"),
                {"pid": provider_id},
            ).one_or_none()
            if agent_ref is not None:
                raise ReferencedEntityInUseError("Provider", provider_id, f"agent '{agent_ref[0]}'")
            for model_row in s.query(ModelTable).filter(ModelTable.provider_id == provider_id):
                _guard_model_in_use(s, model_row.id)
            s.delete(row)  # models cascade via FK

        run_in_session(self._sf, _op)

    def list(self) -> list[ModelProvider]:
        with self._sf() as s:
            rows = s.query(ProviderTable).order_by(ProviderTable.id).all()
            return [self._hydrate(s, r) for r in rows]


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


class SqliteProjectRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def get(self, project_id: str) -> ProjectDefinition:
        with self._sf() as s:
            row = s.get(ProjectTable, project_id)
            if row is None:
                raise KeyError(project_id)
            return ProjectDefinition(id=row.id, name=row.name, repo_url=row.repo_url)

    def add(self, project: ProjectDefinition) -> None:
        def _op(s: Session) -> None:
            if s.get(ProjectTable, project.id) is not None:
                raise EntityAlreadyExistsError("Project", project.id)
            s.add(ProjectTable(id=project.id, name=project.name, repo_url=project.repo_url))

        run_in_session(self._sf, _op)

    def update(self, project: ProjectDefinition) -> None:
        def _op(s: Session) -> None:
            row = s.get(ProjectTable, project.id)
            if row is None:
                raise KeyError(project.id)
            row.name = project.name
            row.repo_url = project.repo_url

        run_in_session(self._sf, _op)

    def delete(self, project_id: str) -> None:
        def _op(s: Session) -> None:
            row = s.get(ProjectTable, project_id)
            if row is not None:
                s.delete(row)

        run_in_session(self._sf, _op)

    def list(self) -> list[ProjectDefinition]:
        with self._sf() as s:
            rows = s.query(ProjectTable).order_by(ProjectTable.id).all()
            return [ProjectDefinition(id=r.id, name=r.name, repo_url=r.repo_url) for r in rows]


# ---------------------------------------------------------------------------
# Two-tier config (scope = 'orchestrator' | <project_id>)
# ---------------------------------------------------------------------------


class SqliteConfigStore:
    ORCHESTRATOR_SCOPE = "orchestrator"

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def get(self, scope: str, key: str) -> str | None:
        with self._sf() as s:
            row = s.get(ConfigTable, (scope, key))
            return None if row is None else row.value

    def set(self, scope: str, key: str, value: str) -> None:
        def _op(s: Session) -> None:
            row = s.get(ConfigTable, (scope, key))
            if row is None:
                s.add(ConfigTable(scope=scope, key=key, value=value))
            else:
                row.value = value

        run_in_session(self._sf, _op)

    def delete(self, scope: str, key: str) -> None:
        def _op(s: Session) -> None:
            row = s.get(ConfigTable, (scope, key))
            if row is not None:
                s.delete(row)

        run_in_session(self._sf, _op)

    def all(self, scope: str) -> dict[str, str]:
        with self._sf() as s:
            rows = s.query(ConfigTable).filter(ConfigTable.scope == scope).all()
            return {r.key: r.value for r in rows}
