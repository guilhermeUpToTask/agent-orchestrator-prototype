"""
src/api/routers/agents.py — Agent registry endpoints.

Covers:
  GET  /agents               list all registered agents
  POST /agents               register or update an agent entry
"""
from __future__ import annotations

from fastapi import APIRouter, status

from src.api.dependencies import AgentRegistryDep, AgentRegisterUseCaseDep
from src.api.schemas.agents import (
    AgentRegisterRequest,
    AgentRegisterResponse,
    AgentResponse,
)
from src.api.schemas.common import ErrorResponse

router = APIRouter(prefix="/agents", tags=["agents"])


# ── Read ──────────────────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=list[AgentResponse],
    summary="List Registered Agents",
    description="Returns all agents currently registered in the agent registry.",
)
def list_agents(registry: AgentRegistryDep) -> list[AgentResponse]:
    return [
        AgentResponse(
            agent_id=a.agent_id,
            name=a.name,
            capabilities=a.capabilities,
            version=a.version,
            trust_level=(
                a.trust_level.value
                if hasattr(a.trust_level, "value")
                else a.trust_level
            ),
            active=a.active,
            max_concurrent_tasks=a.max_concurrent_tasks,
        )
        for a in registry.list_agents()
    ]


# ── Register ──────────────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=AgentRegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register Agent",
    description=(
        "Add or update an agent entry in the registry. "
        "If an agent with the same `agent_id` already exists it is replaced. "
        "`trust_level` valid values: `low`, `medium`, `high`."
    ),
    responses={
        status.HTTP_422_UNPROCESSABLE_ENTITY: {
            "model": ErrorResponse,
            "description": "Invalid field values (e.g. unknown trust_level).",
        }
    },
)
def register_agent(
    payload: AgentRegisterRequest,
    use_case: AgentRegisterUseCaseDep,
) -> AgentRegisterResponse:
    from src.domain import AgentProps  # domain import kept at use-site

    agent_props = AgentProps(
        agent_id=payload.agent_id,
        name=payload.name,
        capabilities=payload.capabilities,
        version=payload.version,
        trust_level=payload.trust_level,
        active=payload.active,
        max_concurrent_tasks=payload.max_concurrent_tasks,
        runtime_type=payload.runtime_type,
    )
    result = use_case.execute(agent_props)
    return AgentRegisterResponse(
        agent_id=result.agent_id,
        active=result.active,
        runtime_type=result.runtime_type,
    )
