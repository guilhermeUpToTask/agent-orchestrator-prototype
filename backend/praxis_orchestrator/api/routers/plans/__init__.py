"""/api/plans — the plan lifecycle: create, inspect, edit, and the human commands
that drive the two gates and the replan loop. Routes map 1:1 onto use cases;
errors bubble to the global mapping layer.

One 1,626-line module became a package of six route modules plus its schemas
(P8.7 task 5). The split is by CONCERN, not by size, and every route path is
byte-identical — `tests/unit/test_capability_matrix.py` compares the served
OpenAPI inventory against the capability matrix, so a path that moved or
disappeared fails the build rather than the next operator.

The composed `router` below is what `api/server.py` includes, exactly as it
included the module before; sub-router order is irrelevant because no two
declare the same path.
"""

from __future__ import annotations

from fastapi import APIRouter

from praxis_orchestrator.api.routers.plans import (
    control,
    conversation,
    cycles,
    lifecycle,
    read,
    telemetry,
)
from praxis_orchestrator.api.routers.plans.schemas import (
    ActiveRunResponse,
    AttemptLogEntryResponse,
    AttemptLogResponse,
    AttemptTimelineResponse,
    BlockResponse,
    ChatMessageResponse,
    CreatePlanRequest,
    CycleDraftRequest,
    EditRequest,
    ExecutionAttemptResponse,
    ExecutionRunTimelineResponse,
    IntentProposalRequest,
    MessageRequest,
    MessageResponse,
    NewTaskBody,
    PlanCreatedResponse,
    PlanDetailResponse,
    PlanningArtifactResponse,
    PlanningOperationResponse,
    ProjectBindingRequest,
    ProviderWaitingResponse,
    PublicationRequest,
    ReviewDecisionRequest,
    TaskExecutionTimelineResponse,
    WorkerLeaseResponse,
    action_endpoints_for,
)
from praxis_orchestrator.api.routers.plans.control import (
    RetryPolicyUpdateRequest,
    update_retry_policy_route,
)

router = APIRouter()
for _module in (lifecycle, read, cycles, control, conversation, telemetry):
    router.include_router(_module.router)

__all__ = [
    "router",
    "ActiveRunResponse",
    "AttemptLogEntryResponse",
    "AttemptLogResponse",
    "AttemptTimelineResponse",
    "BlockResponse",
    "ChatMessageResponse",
    "CreatePlanRequest",
    "CycleDraftRequest",
    "EditRequest",
    "ExecutionAttemptResponse",
    "ExecutionRunTimelineResponse",
    "IntentProposalRequest",
    "MessageRequest",
    "MessageResponse",
    "NewTaskBody",
    "PlanCreatedResponse",
    "PlanDetailResponse",
    "PlanningArtifactResponse",
    "PlanningOperationResponse",
    "ProjectBindingRequest",
    "ProviderWaitingResponse",
    "PublicationRequest",
    "ReviewDecisionRequest",
    "RetryPolicyUpdateRequest",
    "update_retry_policy_route",
    "TaskExecutionTimelineResponse",
    "WorkerLeaseResponse",
    "action_endpoints_for",
]
