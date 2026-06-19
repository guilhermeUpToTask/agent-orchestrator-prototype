"""
src/domain/ports/forge.py — git-forge read port.

One port, two adapters (GitHub primary, local-git fallback). Local git advertises
its gaps via ``capabilities()`` and returns empty PR collections — it never
raises for unsupported operations. GitHub upstream failures raise
ExternalServiceException (mapped to 502 at the API).
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.value_objects.forge import (
    CommitGraph,
    ForgeCapabilities,
    PrState,
    PullRequest,
)


class GitForgePort(ABC):
    @abstractmethod
    def capabilities(self) -> ForgeCapabilities:
        ...

    @abstractmethod
    def commit_graph(self, *, branch: str | None = None, limit: int = 200) -> CommitGraph:
        ...

    @abstractmethod
    def list_prs(self, *, state: PrState | None = None) -> tuple[PullRequest, ...]:
        ...

    @abstractmethod
    def get_pr(self, number: int) -> PullRequest:
        ...

    @abstractmethod
    def pending_reviews(self, *, reviewer: str) -> tuple[PullRequest, ...]:
        ...
