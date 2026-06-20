"""Integration tests for the SQLite config + secret stores (Phase 2).

Uses a temp-file database (not :memory:) so locking/PRAGMA semantics match
production.
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from src.app.errors import ResourceNotFoundException
from src.domain.entities.agent_definition import AgentDefinition
from src.domain.entities.project import Project
from src.domain.errors import ConflictException, ReferentialException
from src.domain.value_objects.config import ProviderKind, RegisteredModel, SecretRef
from src.infra.db.active_project import SqliteActiveProject
from src.infra.db.bootstrap import config_db
from src.infra.db.config_store import SqliteConfigStore
from src.infra.db.secret_store import SqliteSecretStore


@pytest.fixture
def db(tmp_path):
    engine, sf = config_db(tmp_path)
    return engine, sf


@pytest.fixture
def store(db):
    _, sf = db
    return SqliteConfigStore(sf)


@pytest.fixture
def secrets(db):
    _, sf = db
    return SqliteSecretStore(sf, Fernet.generate_key())


def _provider(store: SqliteConfigStore, pid: str = "anthropic", with_model: str | None = None):
    from src.domain.entities.model_provider import ModelProvider
    p = ModelProvider(id=pid, kind=ProviderKind.ANTHROPIC, secret_ref=SecretRef.for_provider(pid))
    if with_model:
        p = p.with_model(RegisteredModel(model_id=with_model, display_name=with_model))
    return store.upsert_provider(p)


class TestPragmas:
    def test_pragmas_applied(self, db) -> None:
        engine, _ = db
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
            assert conn.execute(text("PRAGMA synchronous")).scalar() == 2  # FULL
            assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1


class TestProjectCrud:
    def test_create_get_list(self, store) -> None:
        store.create_project(Project(id="p1", name="One", repo_url="r"))
        assert store.get_project("p1").name == "One"
        assert len(store.list_projects()) == 1

    def test_duplicate_create_is_conflict(self, store) -> None:
        store.create_project(Project(id="p1", name="One", repo_url="r"))
        with pytest.raises(ConflictException) as ei:
            store.create_project(Project(id="p1", name="Dup", repo_url="r"))
        assert ei.value.code == "PROJECT_EXISTS"
        assert len(store.list_projects()) == 1  # no duplicate row

    def test_update_cas_success(self, store) -> None:
        store.create_project(Project(id="p1", name="One", repo_url="r", state_version=0))
        loaded = store.get_project("p1")
        updated = store.update_project(loaded.model_copy(update={"name": "Renamed"}))
        assert updated.name == "Renamed"
        assert updated.state_version == 1

    def test_update_stale_version_conflict(self, store) -> None:
        store.create_project(Project(id="p1", name="One", repo_url="r", state_version=0))
        store.update_project(store.get_project("p1"))  # bumps to v1
        stale = Project(id="p1", name="x", repo_url="r", state_version=0)
        with pytest.raises(ConflictException) as ei:
            store.update_project(stale)
        assert ei.value.context["actual_version"] == 1

    def test_update_missing_raises(self, store) -> None:
        with pytest.raises(ResourceNotFoundException):
            store.update_project(Project(id="ghost", name="x", repo_url="r"))


class TestReferentialIntegrity:
    def test_agent_missing_provider_rejected(self, store) -> None:
        with pytest.raises(ReferentialException):
            store.upsert_agent(
                AgentDefinition(
                    id="a1", name="W", runtime_type="claude",
                    provider_id="ghost", model_id="m",
                )
            )

    def test_delete_referenced_provider_rejected(self, store) -> None:
        _provider(store, "anthropic", with_model="m")
        store.upsert_agent(
            AgentDefinition(
                id="a1", name="W", runtime_type="claude",
                provider_id="anthropic", model_id="m",
            )
        )
        with pytest.raises(ReferentialException):
            store.delete_provider("anthropic")

    def test_delete_unreferenced_provider_ok(self, store) -> None:
        _provider(store, "openai")
        store.delete_provider("openai")
        assert store.get_provider("openai") is None


class TestProviderModels:
    def test_models_round_trip(self, store) -> None:
        _provider(store, "anthropic", with_model="claude-opus-4-8")
        prov = store.get_provider("anthropic")
        assert prov.has_model("claude-opus-4-8")

    def test_upsert_replaces_models(self, store) -> None:
        _provider(store, "anthropic", with_model="old")
        prov = store.get_provider("anthropic")
        store.upsert_provider(prov.with_model(RegisteredModel(model_id="new", display_name="new")))
        refreshed = store.get_provider("anthropic")
        assert {m.model_id for m in refreshed.models} == {"old", "new"}


class TestSecretStore:
    def test_round_trip(self, secrets) -> None:
        ref = SecretRef.for_provider("anthropic")
        secrets.put(ref, "sk-secret-value")
        assert secrets.resolve(ref).get_secret_value() == "sk-secret-value"

    def test_plaintext_never_stored(self, db, secrets) -> None:
        engine, _ = db
        ref = SecretRef.for_provider("anthropic")
        secrets.put(ref, "sk-PLAINTEXT-MARKER")
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT ciphertext, wrapped_key FROM secrets")).all()
        blob = " ".join(str(c) for row in rows for c in row)
        assert "sk-PLAINTEXT-MARKER" not in blob

    def test_resolve_missing_raises(self, secrets) -> None:
        with pytest.raises(ResourceNotFoundException):
            secrets.resolve(SecretRef.for_provider("nope"))

    def test_exists_and_delete(self, secrets) -> None:
        ref = SecretRef.for_provider("x")
        secrets.put(ref, "v")
        assert secrets.exists(ref)
        secrets.delete(ref)
        assert not secrets.exists(ref)


class TestActiveProject:
    def test_set_and_get(self, db) -> None:
        _, sf = db
        ap = SqliteActiveProject(sf)
        assert ap.get_active("cli") is None
        ap.set_active("cli", "p1")
        assert ap.get_active("cli") == "p1"
        ap.set_active("cli", "p2")
        assert ap.get_active("cli") == "p2"
