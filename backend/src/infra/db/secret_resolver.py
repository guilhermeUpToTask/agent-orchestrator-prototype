"""
src/infra/db/secret_resolver.py — overlay stored secrets onto env secrets.

Turns the SQLite secret store into the authoritative source for execution while
keeping the env-based ``SecretSettings`` as a fallback. For each registered
provider, the stored API key (if present) overrides the env value; the active
project's GitHub token does the same. Anything not stored — or unreadable
(missing master key, missing ref) — falls back to env untouched, so dry-run and
env-only deployments keep working.

The result is a plain ``SecretSettings`` the existing runtime/planner/github
factories already consume — no factory signature changes.
"""
from __future__ import annotations

import structlog

from src.domain.errors import BaseAppException
from src.domain.repositories.config_store import ConfigStorePort
from src.domain.value_objects.config import ProviderKind, SecretRef
from src.infra.db.secret_store import SqliteSecretStore
from src.infra.settings.models import SecretSettings

log = structlog.get_logger(__name__)

# ProviderKind -> the SecretSettings field its API key populates.
_KIND_TO_FIELD: dict[ProviderKind, str] = {
    ProviderKind.ANTHROPIC: "anthropic_api_key",
    ProviderKind.OPENAI: "openai_api_key",
    ProviderKind.GEMINI: "gemini_api_key",
    ProviderKind.OPENROUTER: "openrouter_api_key",
}


def resolve_effective_secrets(
    env_secrets: SecretSettings,
    *,
    secret_store: SqliteSecretStore,
    config_store: ConfigStorePort,
    github_ref: SecretRef | None,
) -> SecretSettings:
    """Return SecretSettings with stored provider/GitHub secrets overlaid on env."""
    import dataclasses

    overlay: dict[str, str] = {}

    for provider in config_store.list_providers():
        field = _KIND_TO_FIELD.get(provider.kind)
        if field is None:
            continue
        value = _safe_resolve(secret_store, provider.secret_ref)
        if value:
            overlay[field] = value

    if github_ref is not None:
        token = _safe_resolve(secret_store, github_ref)
        if token:
            overlay["github_token"] = token

    if not overlay:
        return env_secrets
    log.info("secrets.overlay_applied", fields=sorted(overlay.keys()))
    return dataclasses.replace(env_secrets, **overlay)


def _safe_resolve(secret_store: SqliteSecretStore, ref: SecretRef) -> str | None:
    """Resolve a secret, returning None on any expected failure (keep env)."""
    try:
        return secret_store.resolve_plaintext(ref)
    except BaseAppException:
        return None
