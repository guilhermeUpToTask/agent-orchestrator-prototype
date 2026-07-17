from __future__ import annotations

from src.domain.errors.base import DomainError


class ModelNotFoundError(DomainError):
    code = "MODEL_NOT_FOUND"

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        super().__init__(f"Model '{model_id}' not found.", context={"model_id": model_id})


class ModelProviderNotFoundError(DomainError):
    code = "PROVIDER_NOT_FOUND"

    def __init__(self, provider_id: str) -> None:
        self.provider_id = provider_id
        super().__init__(
            f"Model provider '{provider_id}' not found.",
            context={"provider_id": provider_id},
        )


class CapabilityNotFoundError(DomainError):
    code = "CAPABILITY_NOT_FOUND"

    def __init__(self, capability_id: str) -> None:
        self.capability_id = capability_id
        super().__init__(
            f"Capability '{capability_id}' not found.",
            context={"capability_id": capability_id},
        )


class EntityAlreadyExistsError(DomainError):
    """Create rejected because an entity with this id already exists."""

    code = "ENTITY_ALREADY_EXISTS"

    def __init__(self, entity: str, entity_id: str) -> None:
        self.entity = entity
        self.entity_id = entity_id
        super().__init__(
            f"{entity} '{entity_id}' already exists.",
            context={"entity": entity, "entity_id": entity_id},
        )


class ReferencedEntityInUseError(DomainError):
    """Delete-guard: refuse to delete reference data still in use by something active."""

    code = "ENTITY_IN_USE"

    def __init__(self, entity: str, entity_id: str, used_by: str) -> None:
        self.entity = entity
        self.entity_id = entity_id
        self.used_by = used_by
        super().__init__(
            f"Cannot delete {entity} '{entity_id}': still referenced by {used_by}.",
            context={"entity": entity, "entity_id": entity_id, "used_by": used_by},
        )
