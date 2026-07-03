"""Envelope-encrypted secret store on real SQLite: round-trip, fail-closed
master-key handling, and the no-plaintext-at-rest guarantee."""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from src.infra.db.engine import build_engine, make_session_factory
from src.infra.db.secret_ref import SecretRef
from src.infra.db.secret_store import SqliteSecretStore, load_master_key
from src.infra.db.tables import Base
from src.infra.errors import InfrastructureError, SecretNotFoundError

pytestmark = pytest.mark.integration


@pytest.fixture
def sf(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'secrets.db'}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


@pytest.fixture
def master_key():
    return Fernet.generate_key()


def test_put_resolve_roundtrip_and_overwrite(sf, master_key):
    store = SqliteSecretStore(sf, master_key)
    ref = SecretRef.for_provider("prov1")
    store.put(ref, "sk-first")
    assert store.resolve(ref).get_secret_value() == "sk-first"
    assert store.exists(ref)
    store.put(ref, "sk-second")  # overwrite re-encrypts
    assert store.resolve_plaintext(ref) == "sk-second"


def test_plaintext_never_at_rest(sf, master_key):
    store = SqliteSecretStore(sf, master_key)
    store.put(SecretRef.for_provider("prov1"), "sk-super-secret")
    with sf() as s:
        row = s.execute(
            text("SELECT ciphertext, wrapped_key FROM secrets")
        ).one()
    assert "sk-super-secret" not in row[0]
    assert "sk-super-secret" not in row[1]


def test_missing_secret_raises_not_found(sf, master_key):
    store = SqliteSecretStore(sf, master_key)
    with pytest.raises(SecretNotFoundError):
        store.resolve(SecretRef.for_provider("ghost"))


def test_delete_removes_secret(sf, master_key):
    store = SqliteSecretStore(sf, master_key)
    ref = SecretRef.for_provider("prov1")
    store.put(ref, "sk")
    store.delete(ref)
    assert not store.exists(ref)


def test_wrong_master_key_fails_decrypt_not_garbage(sf, master_key):
    store = SqliteSecretStore(sf, master_key)
    ref = SecretRef.for_provider("prov1")
    store.put(ref, "sk")
    other = SqliteSecretStore(sf, Fernet.generate_key())
    with pytest.raises(InfrastructureError):
        other.resolve(ref)


def test_master_key_loading_fails_closed(monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_MASTER_KEY", raising=False)
    with pytest.raises(InfrastructureError):
        load_master_key()
    monkeypatch.setenv("ORCHESTRATOR_MASTER_KEY", "not-a-fernet-key")
    with pytest.raises(InfrastructureError):
        load_master_key()
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("ORCHESTRATOR_MASTER_KEY", key)
    assert load_master_key() == key.encode()


def test_secret_ref_uri_contract():
    with pytest.raises(ValueError):
        SecretRef(uri="plain://nope")
    assert SecretRef.for_provider("p1").uri == "secret://provider/p1"
