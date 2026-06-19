"""
src/api/routers/providers.py — model-provider control-plane endpoints.

Thin: validate -> RegistryService -> response DTO. API keys are write-only and
never echoed back.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, status

from src.api.dependencies import RegistryServiceDep
from src.api.schemas.control import (
    ModelCreateRequest,
    ProviderCreateRequest,
    ProviderResponse,
)
from src.api.security import require_api_token

if TYPE_CHECKING:
    from src.app.services.registry_service import RegistryService

router = APIRouter(
    prefix="/providers",
    tags=["providers"],
    dependencies=[Depends(require_api_token)],
)


@router.get("", response_model=list[ProviderResponse], summary="List Providers")
def list_providers(svc: RegistryServiceDep) -> list[ProviderResponse]:
    service: "RegistryService" = svc  # type: ignore[assignment]
    return [ProviderResponse.from_domain(p) for p in service.list_providers()]


@router.post(
    "",
    response_model=ProviderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register Provider",
)
def register_provider(body: ProviderCreateRequest, svc: RegistryServiceDep) -> ProviderResponse:
    service: "RegistryService" = svc  # type: ignore[assignment]
    provider = service.register_provider(
        provider_id=body.id,
        kind=body.kind,
        api_key=body.api_key,
        base_url=body.base_url,
        default_model=body.default_model,
    )
    return ProviderResponse.from_domain(provider)


@router.post(
    "/{provider_id}/models",
    response_model=ProviderResponse,
    summary="Register Model on Provider",
)
def add_model(
    provider_id: str, body: ModelCreateRequest, svc: RegistryServiceDep
) -> ProviderResponse:
    service: "RegistryService" = svc  # type: ignore[assignment]
    provider = service.add_model(
        provider_id=provider_id,
        model_id=body.model_id,
        display_name=body.display_name,
        capabilities=tuple(body.capabilities),
    )
    return ProviderResponse.from_domain(provider)


@router.delete(
    "/{provider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete Provider",
)
def delete_provider(provider_id: str, svc: RegistryServiceDep) -> None:
    service: "RegistryService" = svc  # type: ignore[assignment]
    service.delete_provider(provider_id)
