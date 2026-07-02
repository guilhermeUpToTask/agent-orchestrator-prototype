"""Repository ports (interfaces). Implementations live in infra and have a
factory injected; these ports stay pure domain. The repo USES the factory to
reconstruct entities from persisted rows — the dependency points repo -> factory,
never the reverse (a pure factory must not depend on an impure repo)."""

from __future__ import annotations

from typing import Protocol, TypeVar

T = TypeVar("T")


class Repository(Protocol[T]):
    def get(self, id: str) -> T: ...
    def list(self) -> list[T]: ...
    def add(self, entity: T) -> None: ...
    def update(self, entity: T) -> None: ...
    def delete(self, id: str) -> None: ...
