"""Tests for the effective-secrets overlay (F1)."""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from src.app.services.registry_service import RegistryService
from src.domain.value_objects.config import ProviderKind, SecretRef
from src.infra.db.bootstrap import config_db
from src.infra.db.config_store import SqliteConfigStore
from src.infra.db.secret_resolver import resolve_effective_secrets
from src.infra.db.secret_store import SqliteSecretStore
from src.infra.settings.models import SecretSettings


@pytest.fixture
def wired(tmp_path):
    _, sf = config_db(tmp_path)
    config = SqliteConfigStore(sf)
    secrets = SqliteSecretStore(sf, Fernet.generate_key())
    registry = RegistryService(config, secrets)
    return config, secrets, registry


def test_stored_provider_key_overrides_env(wired) -> None:
    config, secrets, registry = wired
    registry.register_provider(
        provider_id="anthropic", kind=ProviderKind.ANTHROPIC, api_key="STORED-KEY"
    )
    env = SecretSettings(anthropic_api_key="ENV-KEY", openai_api_key="ENV-OPENAI")
    eff = resolve_effective_secrets(env, secret_store=secrets, config_store=config, github_ref=None)
    assert eff.anthropic_api_key == "STORED-KEY"   # SQLite wins
    assert eff.openai_api_key == "ENV-OPENAI"       # untouched -> env fallback


def test_no_providers_returns_env_unchanged(wired) -> None:
    config, secrets, _ = wired
    env = SecretSettings(anthropic_api_key="ENV-KEY")
    eff = resolve_effective_secrets(env, secret_store=secrets, config_store=config, github_ref=None)
    assert eff is env  # identity: nothing overlaid


def test_missing_ref_keeps_env(wired) -> None:
    config, secrets, _ = wired
    # Provider row exists but its secret was never stored / was deleted.
    from src.domain.entities.model_provider import ModelProvider

    config.upsert_provider(
        ModelProvider(
            id="anthropic", kind=ProviderKind.ANTHROPIC,
            secret_ref=SecretRef.for_provider("anthropic"),
        )
    )
    env = SecretSettings(anthropic_api_key="ENV-KEY")
    eff = resolve_effective_secrets(env, secret_store=secrets, config_store=config, github_ref=None)
    assert eff.anthropic_api_key == "ENV-KEY"  # unresolved ref -> fallback, no crash


def test_github_ref_overlaid(wired) -> None:
    config, secrets, _ = wired
    ref = SecretRef.for_project_github("p1")
    secrets.put(ref, "ghp_STORED")
    env = SecretSettings(github_token="ghp_ENV")
    eff = resolve_effective_secrets(env, secret_store=secrets, config_store=config, github_ref=ref)
    assert eff.github_token == "ghp_STORED"
