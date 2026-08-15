"""Where a project's forge binding is stored.

Deliberately NOT fields on `ProjectDefinition`: that is a frozen-domain entity,
and adding to it would need a decision-log entry and an explicit un-freeze. The
config store is already two-tier with a project id as a scope, so the binding
fits with no domain change at all.

This module is the ONE place the three key names live.
"""

from __future__ import annotations

from dataclasses import dataclass

from praxis_orchestrator.infra.db.reference_repos import SqliteConfigStore
from praxis_orchestrator.infra.db.secret_ref import SecretRef

PROVIDER_KEY = "forge.provider"
REPOSITORY_KEY = "forge.repository"
TOKEN_REF_KEY = "forge.token_ref"


@dataclass(frozen=True)
class ForgeBinding:
    provider: str
    repository: str
    token_ref: str


def read_binding(config_store: SqliteConfigStore, project_id: str) -> ForgeBinding | None:
    provider = config_store.get(project_id, PROVIDER_KEY)
    repository = config_store.get(project_id, REPOSITORY_KEY)
    token_ref = config_store.get(project_id, TOKEN_REF_KEY)
    if not provider or not repository or not token_ref:
        return None
    return ForgeBinding(provider=provider, repository=repository, token_ref=token_ref)


def write_binding(
    config_store: SqliteConfigStore, project_id: str, repository: str
) -> ForgeBinding:
    token_ref = SecretRef.for_forge(project_id).uri
    config_store.set(project_id, PROVIDER_KEY, "github")
    config_store.set(project_id, REPOSITORY_KEY, repository)
    config_store.set(project_id, TOKEN_REF_KEY, token_ref)
    return ForgeBinding(provider="github", repository=repository, token_ref=token_ref)


def clear_binding(config_store: SqliteConfigStore, project_id: str) -> None:
    for key in (PROVIDER_KEY, REPOSITORY_KEY, TOKEN_REF_KEY):
        config_store.delete(project_id, key)
