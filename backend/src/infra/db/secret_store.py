"""
src/infra/db/secret_store.py — SQLite implementation of SecretStorePort.

Envelope encryption: each secret gets its own random Fernet *data key*; the
plaintext is encrypted with the data key, and the data key is wrapped
(encrypted) with the *master key* from the server environment. The table holds
ciphertext + wrapped key only — never plaintext, never the master key.

This is the *only* module that decrypts. ``resolve()`` returns a ``SecretStr``;
the single ``.get_secret_value()`` crossing in the whole codebase lives here.
Master-key rotation re-wraps data keys (cheap) without touching ciphertext.
"""
from __future__ import annotations

import os

import structlog
from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr
from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

from src.app.errors import InfrastructureException, ResourceNotFoundException
from src.domain.repositories.secret_store import SecretStorePort
from src.domain.value_objects.config import SecretRef
from src.infra.db.tables import SecretTable

log = structlog.get_logger(__name__)

MASTER_KEY_ENV = "ORCHESTRATOR_MASTER_KEY"


def load_master_key() -> bytes:
    """
    Read the Fernet master key from the environment.

    Raises InfrastructureException with an actionable message if missing or
    malformed — secret storage must fail closed, never with a silent default.
    """
    raw = os.environ.get(MASTER_KEY_ENV, "").strip()
    if not raw:
        raise InfrastructureException(
            f"{MASTER_KEY_ENV} is not set. Generate one with "
            "`python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"` and export it.",
            code="MASTER_KEY_MISSING",
        )
    try:
        Fernet(raw.encode())  # validate shape
    except (ValueError, TypeError) as exc:
        raise InfrastructureException(
            f"{MASTER_KEY_ENV} is not a valid Fernet key", code="MASTER_KEY_INVALID"
        ) from exc
    return raw.encode()


class SqliteSecretStore(SecretStorePort):
    def __init__(self, session_factory: sessionmaker[Session], master_key: bytes) -> None:
        self._sf = session_factory
        self._master = Fernet(master_key)

    def put(self, ref: SecretRef, plaintext: str) -> None:
        data_key = Fernet.generate_key()
        ciphertext = Fernet(data_key).encrypt(plaintext.encode()).decode()
        wrapped_key = self._master.encrypt(data_key).decode()
        with self._sf() as s:
            existing = s.get(SecretTable, ref.uri)
            if existing is not None:
                existing.ciphertext = ciphertext
                existing.wrapped_key = wrapped_key
            else:
                s.add(SecretTable(uri=ref.uri, ciphertext=ciphertext, wrapped_key=wrapped_key))
            s.commit()
        log.info("secret.stored", uri=ref.uri)

    def resolve(self, ref: SecretRef) -> SecretStr:
        with self._sf() as s:
            row = s.get(SecretTable, ref.uri)
        if row is None:
            raise ResourceNotFoundException(
                f"Secret '{ref.uri}' not found", code="SECRET_NOT_FOUND"
            )
        try:
            data_key = self._master.decrypt(row.wrapped_key.encode())
            plaintext = Fernet(data_key).decrypt(row.ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise InfrastructureException(
                f"Secret '{ref.uri}' could not be decrypted (wrong master key?)",
                code="SECRET_DECRYPT_FAILED",
            ) from exc
        return SecretStr(plaintext)

    def exists(self, ref: SecretRef) -> bool:
        with self._sf() as s:
            return s.get(SecretTable, ref.uri) is not None

    def delete(self, ref: SecretRef) -> None:
        with self._sf() as s:
            s.execute(delete(SecretTable).where(SecretTable.uri == ref.uri))
            s.commit()
