"""
/api/metrics — the global (or per-plan) telemetry roll-up.

Aggregates the agent_events stream (decision #33: no separate metrics store) into
the numbers the run's failure modes need visible: LLM sessions/calls and token
usage (from the reasoner's llm.call rows) and agent run/failure counts grouped by
FailureKind — so a rate-limit storm is one number, not a scroll through the feed.
Always 200; token-guarded like the other control-plane status endpoints.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.dependencies import get_container
from src.api.security import require_api_token
from src.infra.container import AppContainer

router = APIRouter(
    prefix="/metrics",
    dependencies=[Depends(require_api_token)],
    tags=["metrics"],
)


class LlmMetrics(BaseModel):
    sessions: int
    calls: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class AgentMetrics(BaseModel):
    runs: int
    finished: int
    failed: int
    failures_by_kind: dict[str, int]


class MetricsResponse(BaseModel):
    llm: LlmMetrics
    agent: AgentMetrics


@router.get("")
def metrics(
    plan_id: str | None = None,
    container: AppContainer = Depends(get_container),
) -> MetricsResponse:
    data = container.agent_event_reader.metrics(plan_id)
    return MetricsResponse(
        llm=LlmMetrics(**data["llm"]),
        agent=AgentMetrics(**data["agent"]),
    )
