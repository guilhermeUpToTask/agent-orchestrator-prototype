"""
src/infra/reasoner/factory.py — build the Reasoner from the providers catalog.

Two modes, selected by the config key `reasoner.mode` (scope 'orchestrator'):

  stub (default) — the deterministic StubReasoner. NEVER touches the secret
                   store, so dry-run works without ORCHESTRATOR_MASTER_KEY.
  llm            — the OpenAIReasoner. Credentials/model resolve through the
                   catalog: `reasoner.provider_id` -> providers row (base_url
                   + api_key_ref -> secret store), `reasoner.model_id` ->
                   models row (the provider model string). Resolution fails
                   fast with REASONER_CONFIG_INVALID and an actionable message.

Config keys (scope 'orchestrator'):
  reasoner.mode         stub | llm                      (default stub)
  reasoner.provider_id  providers.id                    (llm mode, required)
  reasoner.model_id     models.id                       (llm mode, required)
  reasoner.temperature  float                           (default 0.2)
  reasoner.max_turns    int, converse budget            (default 8)
"""
from __future__ import annotations

from typing import Callable

from src.domain.ports.reasoner_port import Reasoner
from src.domain.repositories.capability_repo import CapabilityRepository
from src.infra.db.reference_repos import (
    SqliteConfigStore,
    SqliteModelProviderRepository,
    SqliteModelRepository,
)
from src.infra.db.secret_ref import SecretRef
from src.infra.db.secret_store import SqliteSecretStore
from src.infra.errors import InfrastructureError
from src.infra.reasoner.openai_reasoner import OpenAIReasoner
from src.infra.reasoner.runtime.llm_client import OpenAIChatClient
from src.infra.reasoner.stub_reasoner import StubReasoner

REASONER_CONFIG_INVALID = "REASONER_CONFIG_INVALID"

_SCOPE = SqliteConfigStore.ORCHESTRATOR_SCOPE


def _invalid(message: str) -> InfrastructureError:
    return InfrastructureError(message, code=REASONER_CONFIG_INVALID)


def build_reasoner(
    config_store: SqliteConfigStore,
    provider_repo: SqliteModelProviderRepository,
    model_repo: SqliteModelRepository,
    secret_store: Callable[[], SqliteSecretStore],
    capability_repo: CapabilityRepository,
) -> Reasoner:
    """`secret_store` is a thunk: stub mode must never construct it (it fails
    closed on a missing master key, which dry-run does not have)."""
    mode = (config_store.get(_SCOPE, "reasoner.mode") or "stub").strip().lower()
    if mode == "stub":
        return StubReasoner()
    if mode != "llm":
        raise _invalid(
            f"reasoner.mode is '{mode}' — valid values are 'stub' or 'llm'. "
            "Fix it with: orchestrate config set reasoner.mode stub|llm"
        )

    provider_id = (config_store.get(_SCOPE, "reasoner.provider_id") or "").strip()
    if not provider_id:
        raise _invalid(
            "reasoner.mode is 'llm' but reasoner.provider_id is not set. "
            "Seed one with `orchestrate seed demo --provider ...` or set it "
            "with `orchestrate config set reasoner.provider_id <id>`."
        )
    model_id = (config_store.get(_SCOPE, "reasoner.model_id") or "").strip()
    if not model_id:
        raise _invalid(
            "reasoner.mode is 'llm' but reasoner.model_id is not set. "
            "Set it with `orchestrate config set reasoner.model_id <id>`."
        )

    try:
        provider = provider_repo.get(provider_id)
    except Exception as exc:
        raise _invalid(
            f"reasoner.provider_id '{provider_id}' does not exist in the "
            "providers catalog."
        ) from exc
    try:
        model = model_repo.get(model_id)
    except Exception as exc:
        raise _invalid(
            f"reasoner.model_id '{model_id}' does not exist in the models "
            "catalog."
        ) from exc
    if model.provider_id != provider.id:
        raise _invalid(
            f"model '{model_id}' belongs to provider '{model.provider_id}', "
            f"not the configured provider '{provider_id}'."
        )

    api_key = secret_store().resolve_plaintext(SecretRef(uri=provider.api_key_ref))

    temperature = float(config_store.get(_SCOPE, "reasoner.temperature") or 0.2)
    max_turns = int(config_store.get(_SCOPE, "reasoner.max_turns") or 8)

    client = OpenAIChatClient(
        api_key=api_key,
        model=model.name,
        base_url=provider.base_url or None,
        temperature=temperature,
    )
    return OpenAIReasoner(
        client,
        capability_repo.list(),
        converse_max_turns=max_turns,
    )
