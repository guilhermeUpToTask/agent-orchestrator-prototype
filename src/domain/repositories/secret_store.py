"""
src/domain/repositories/secret_store.py — Secret persistence port.

The store holds ciphertext only. ``resolve()`` is the single point at which a
secret is decrypted, and it returns a ``SecretStr`` so the plaintext cannot leak
through reprs/logs by accident. Callers that genuinely need the raw value call
``.get_secret_value()`` — which, per the secret discipline, happens in exactly
one place in the infra layer.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import SecretStr

from src.domain.value_objects.config import SecretRef


class SecretStorePort(ABC):
    @abstractmethod
    def put(self, ref: SecretRef, plaintext: str) -> None:
        """Encrypt and store the secret under ``ref`` (insert or replace)."""
        ...

    @abstractmethod
    def resolve(self, ref: SecretRef) -> SecretStr:
        """Decrypt and return the secret. Raises ResourceNotFoundException if
        the ref is unknown."""
        ...

    @abstractmethod
    def exists(self, ref: SecretRef) -> bool:
        ...

    @abstractmethod
    def delete(self, ref: SecretRef) -> None:
        ...
