from __future__ import annotations
from pydantic import BaseModel

from src.domain.entities.capability import Capability
from src.domain.policies.retry_policies import RetryPolicy


class AgentSpec(BaseModel):
    """Definition of an agent: who it is, what it can do, how it retries,
    and which runtime executes it.

    `role` is the agent's functional job in the orchestration (e.g. "test_writer",
    "implementer", "reviewer"). `model_role` is an indirection key into the
    provider/model catalog naming a model *tier* (e.g. "cheap", "smart",
    "long_context") resolved to a concrete IAModel at runtime — so swapping the
    model behind a tier does not require touching every agent that uses it.

    Runtime resolution (deliberate Phase-0 un-freeze, 2026-07-05): the agent
    registry is the authority on which runtime an agent resolves to —
    `runtime_type` picks the CLI runtime (or the dry-run dummy), and
    `provider_id`/`model_id` point into the providers/models catalog for the
    credentials and model string. Infra validates the wiring; the entity only
    carries it.
    """

    id: str
    name: str
    role: str
    model_role: str
    instructions: str
    capabilities: list[Capability] = []
    default_retry: RetryPolicy
    runtime_type: str = "pi"  # pi | claude | gemini | dry-run
    provider_id: str | None = None  # FK -> ModelProvider.id
    model_id: str | None = None  # FK -> IAModel.id
