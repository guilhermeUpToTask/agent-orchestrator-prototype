"""
src/api/routers/agent_definitions.py — global agent-definition control plane.

Distinct from /agents (the AgentProps runtime registry): these are the durable
global definitions (provider + model) an operator manages. Thin over
RegistryService.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, status

from src.api.dependencies import RegistryServiceDep
from src.api.schemas.control import (
    AgentDefinitionCreateRequest,
    AgentDefinitionResponse,
)
from src.api.security import require_api_token

if TYPE_CHECKING:
    from src.app.services.registry_service import RegistryService

router = APIRouter(
    prefix="/agent-definitions",
    tags=["agent-definitions"],
    dependencies=[Depends(require_api_token)],
)


@router.get("", response_model=list[AgentDefinitionResponse], summary="List Agent Definitions")
def list_agent_definitions(svc: RegistryServiceDep) -> list[AgentDefinitionResponse]:
    service: "RegistryService" = svc  # type: ignore[assignment]
    return [AgentDefinitionResponse.from_domain(a) for a in service.list_agents()]


@router.post(
    "",
    response_model=AgentDefinitionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register Agent Definition",
)
def register_agent_definition(
    body: AgentDefinitionCreateRequest, svc: RegistryServiceDep
) -> AgentDefinitionResponse:
    service: "RegistryService" = svc  # type: ignore[assignment]
    agent = service.register_agent(
        agent_id=body.id,
        name=body.name,
        runtime_type=body.runtime_type,
        provider_id=body.provider_id,
        model_id=body.model_id,
        capabilities=tuple(body.capabilities),
    )
    return AgentDefinitionResponse.from_domain(agent)


@router.delete(
    "/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete Agent Definition",
)
def delete_agent_definition(agent_id: str, svc: RegistryServiceDep) -> None:
    service: "RegistryService" = svc  # type: ignore[assignment]
    service.delete_agent(agent_id)
