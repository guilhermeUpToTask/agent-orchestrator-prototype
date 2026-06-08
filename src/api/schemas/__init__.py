"""src/api/schemas/ — Pydantic V2 API DTOs (Request + Response)."""

from src.api.schemas.common import ErrorResponse, HealthResponse
from src.api.schemas.plan import (
    ApproveBriefResponse,
    ApproveArchitectureRequest,
    ApproveArchitectureResponse,
    ApprovePhaseRequest,
    ApprovePhaseResponse,
    PlanResponse,
    PlanPhaseResponse,
    PlanBriefResponse,
    PlanHistoryEntryResponse,
)
from src.api.schemas.goals import (
    GoalResponse,
    GoalTaskResponse,
    GoalHistoryEntryResponse,
    GoalFinalizeResponse,
)
from src.api.schemas.tasks import (
    TaskRetryRequest,
    TaskRetryResponse,
    TaskDeleteResponse,
    TaskPruneRequest,
    TaskPruneResponse,
    TaskAssignResponse,
    TaskUnblockResponse,
    TaskFailHandlingResponse,
)
from src.api.schemas.agents import (
    AgentResponse,
    AgentRegisterRequest,
    AgentRegisterResponse,
)
from src.api.schemas.refinement import (
    RefineRequest,
    RefineResponse,
)
from src.api.schemas.discovery import (
    DiscoveryMessageRequest,
    DiscoveryMessageResponse,
    DiscoveryStartResponse,
)
from src.api.schemas.project import (
    ProjectResetRequest,
    ProjectResetResponse,
)
from src.api.schemas.spec import (
    SpecResponse,
    ProposeSpecChangeRequest,
    ProposeSpecChangeResponse,
    ValidateSpecRequest,
    ValidateSpecResponse,
)

__all__ = [
    "ErrorResponse",
    "HealthResponse",
    # Plan
    "ApproveBriefResponse",
    "ApproveArchitectureRequest",
    "ApproveArchitectureResponse",
    "ApprovePhaseRequest",
    "ApprovePhaseResponse",
    "PlanResponse",
    "PlanPhaseResponse",
    "PlanBriefResponse",
    "PlanHistoryEntryResponse",
    # Goals
    "GoalResponse",
    "GoalTaskResponse",
    "GoalHistoryEntryResponse",
    "GoalFinalizeResponse",
    # Tasks
    "TaskRetryRequest",
    "TaskRetryResponse",
    "TaskDeleteResponse",
    "TaskPruneRequest",
    "TaskPruneResponse",
    "TaskAssignResponse",
    "TaskUnblockResponse",
    "TaskFailHandlingResponse",
    # Agents
    "AgentResponse",
    "AgentRegisterRequest",
    "AgentRegisterResponse",
    # Refinement
    "RefineRequest",
    "RefineResponse",
    # Discovery
    "DiscoveryMessageRequest",
    "DiscoveryMessageResponse",
    "DiscoveryStartResponse",
    # Project
    "ProjectResetRequest",
    "ProjectResetResponse",
    # Spec
    "SpecResponse",
    "ProposeSpecChangeRequest",
    "ProposeSpecChangeResponse",
    "ValidateSpecRequest",
    "ValidateSpecResponse",
]
