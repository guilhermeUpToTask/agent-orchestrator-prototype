"""
src/app/services/registry_service.py — Global registry application service.

Manages the GLOBAL config entities: model providers (+ their API keys and
models) and agent definitions. Shared by CLI and API. Validates cross-entity
references (an agent must point at an existing provider+model) and raises the
shared exception taxonomy only.
"""
from __future__ import annotations

import structlog

from src.app.errors import ResourceNotFoundException, ValidationException
from src.domain.entities.agent_definition import AgentDefinition
from src.domain.entities.model_provider import ModelProvider
from src.domain.repositories.agent_registry import AgentRegistryPort
from src.domain.repositories.config_store import ConfigStorePort
from src.domain.repositories.secret_store import SecretStorePort
from src.domain.services.agent_mapping import agent_definition_to_props
from src.domain.value_objects.capability import CapabilityTag
from src.domain.value_objects.config import ProviderKind, RegisteredModel, SecretRef

log = structlog.get_logger(__name__)


class RegistryService:
    def __init__(
        self,
        config_store: ConfigStorePort,
        secret_store: SecretStorePort,
        agent_registry: AgentRegistryPort | None = None,
    ) -> None:
        self._config = config_store
        self._secrets = secret_store
        # Optional: when provided, registering a definition also writes the
        # derived runtime AgentProps into the registry the scheduler reads, so a
        # config-registered agent becomes schedulable (liveness still requires a
        # running worker to heartbeat it).
        self._agent_registry = agent_registry

    # -- Providers ------------------------------------------------------------
    def register_provider(
        self,
        *,
        provider_id: str,
        kind: ProviderKind,
        api_key: str,
        base_url: str | None = None,
        default_model: str | None = None,
    ) -> ModelProvider:
        secret_ref = SecretRef.for_provider(provider_id)
        self._secrets.put(secret_ref, api_key)
        provider = ModelProvider(
            id=provider_id,
            kind=kind,
            secret_ref=secret_ref,
            base_url=base_url,
            default_model=default_model,
        )
        saved = self._config.upsert_provider(provider)
        log.info("provider.registered", provider_id=saved.id, kind=saved.kind.value)
        return saved

    def get_provider(self, provider_id: str) -> ModelProvider:
        provider = self._config.get_provider(provider_id)
        if provider is None:
            raise ResourceNotFoundException(
                f"Provider '{provider_id}' not found", code="PROVIDER_NOT_FOUND"
            )
        return provider

    def list_providers(self) -> tuple[ModelProvider, ...]:
        return self._config.list_providers()

    def delete_provider(self, provider_id: str) -> None:
        self.get_provider(provider_id)
        self._config.delete_provider(provider_id)
        log.info("provider.deleted", provider_id=provider_id)

    def add_model(
        self,
        *,
        provider_id: str,
        model_id: str,
        display_name: str | None = None,
        capabilities: tuple[str, ...] = (),
    ) -> ModelProvider:
        provider = self.get_provider(provider_id)
        model = RegisteredModel(
            model_id=model_id,
            display_name=display_name or model_id,
            capabilities=capabilities,
        )
        updated = self._config.upsert_provider(provider.with_model(model))
        log.info("provider.model_added", provider_id=provider_id, model_id=model_id)
        return updated

    # -- Agent definitions ----------------------------------------------------
    def register_agent(
        self,
        *,
        agent_id: str,
        name: str,
        runtime_type: str,
        provider_id: str,
        model_id: str,
        capabilities: tuple[CapabilityTag, ...] = (),
    ) -> AgentDefinition:
        provider = self.get_provider(provider_id)
        if not provider.has_model(model_id):
            raise ValidationException(
                f"Provider '{provider_id}' does not offer model '{model_id}'",
                code="MODEL_NOT_REGISTERED",
                context={"provider_id": provider_id, "model_id": model_id},
            )
        definition = AgentDefinition(
            id=agent_id,
            name=name,
            capabilities=capabilities,
            runtime_type=runtime_type,
            provider_id=provider_id,
            model_id=model_id,
        )
        saved = self._config.upsert_agent(definition)
        if self._agent_registry is not None:
            # Write-through to the runtime registry so the scheduler can see it.
            self._agent_registry.register(agent_definition_to_props(saved, provider))
        log.info("agent.registered", agent_id=saved.id, provider_id=provider_id)
        return saved

    def get_agent(self, agent_id: str) -> AgentDefinition:
        agent = self._config.get_agent(agent_id)
        if agent is None:
            raise ResourceNotFoundException(
                f"Agent '{agent_id}' not found", code="AGENT_NOT_FOUND"
            )
        return agent

    def list_agents(self) -> tuple[AgentDefinition, ...]:
        return self._config.list_agents()

    def delete_agent(self, agent_id: str) -> None:
        self.get_agent(agent_id)
        self._config.delete_agent(agent_id)
        if self._agent_registry is not None:
            self._agent_registry.deregister(agent_id)
        log.info("agent.deleted", agent_id=agent_id)
