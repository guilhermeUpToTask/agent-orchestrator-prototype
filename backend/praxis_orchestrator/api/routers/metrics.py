"""
/api/metrics — the global (or per-plan) telemetry roll-up.

Aggregates the agent_events stream (decision #33: no separate metrics store) into
the numbers the run's failure modes need visible: LLM sessions/calls and token
usage (from the reasoner's llm.call rows) and agent run/failure counts grouped by
FailureKind — so a rate-limit storm is one number, not a scroll through the feed.
Always 200; token-guarded like every other router (applied at mount time in
`server.py::create_app`).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from praxis_orchestrator.api.dependencies import get_container
from praxis_orchestrator.infra.container import AppContainer

router = APIRouter(
    prefix="/metrics",
    tags=["metrics"],
)


class CoverageMetrics(BaseModel):
    observations: int
    reported: int
    estimated: int
    unavailable: int
    legacy_unknown: int


class UsageScopeMetrics(BaseModel):
    sessions: int
    calls: int
    # Turn consumption per scope. `max_session_turns` is the diagnostic one: a
    # session budget only warns by exhausting, and a single session creeping
    # toward the configured `reasoner.max_turns` is the signal before a goal
    # blocks on "exceeded N turns without submitting".
    turns: int
    max_session_turns: int
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    coverage: CoverageMetrics


class LlmMetrics(UsageScopeMetrics):
    scopes: dict[str, UsageScopeMetrics]


class AgentMetrics(BaseModel):
    runs: int
    finished: int
    failed: int
    failures_by_kind: dict[str, int]
    source: str
    quality: str


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
