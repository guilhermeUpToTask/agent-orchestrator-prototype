from __future__ import annotations
from pydantic import BaseModel

from src.domain.entities.capability import Capability
from src.domain.policies.retry_policies import RetryPolicy


class AgentSpec(BaseModel):
    """Definition of an agent: who it is, what it can do, how it retries.

    `role` is the agent's functional job in the orchestration (e.g. "test_writer",
    "implementer", "reviewer"). `model_role` is an indirection key into the
    provider/model catalog naming a model *tier* (e.g. "cheap", "smart",
    "long_context") resolved to a concrete IAModel at runtime — so swapping the
    model behind a tier does not require touching every agent that uses it.
    """

    id: str
    name: str
    role: str
    model_role: str
    instructions: str
    capabilities: list[Capability] = []
    default_retry: RetryPolicy
