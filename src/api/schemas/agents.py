"""src/api/schemas/agents.py — Agent-related API DTOs."""
from __future__ import annotations


from pydantic import BaseModel, Field


class AgentResponse(BaseModel):
    """Full agent read-model."""
    agent_id: str
    name: str
    capabilities: list[str]
    version: str
    trust_level: str
    active: bool
    max_concurrent_tasks: int


class AgentRegisterRequest(BaseModel):
    agent_id: str = Field(max_length=100)
    name: str = Field(max_length=200)
    capabilities: list[str]
    version: str = Field(max_length=50)
    trust_level: str = Field(default="low")
    active: bool = True
    max_concurrent_tasks: int = Field(default=1, ge=1, le=32)
    runtime_type: str = Field(max_length=100)


class AgentRegisterResponse(BaseModel):
    agent_id: str
    active: bool
    runtime_type: str
