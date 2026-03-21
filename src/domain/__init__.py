"""
src/domain/ — Domain layer public API.

Import anything domain-related from here.

  from src.domain import TaskAggregate, TaskStatus, AgentProps, DomainEvent ...
"""

# Status value objects
from src.domain.value_objects.status import TaskStatus, TrustLevel

# Events
from src.domain.events.domain_event import DomainEvent

# Execution value objects
from src.domain.value_objects.execution import AgentExecutionResult, ExecutionContext

# Errors
from src.domain.errors import (
    DomainError,
    ForbiddenFileEditError,
    InvalidStatusTransitionError,
    MaxRetriesExceededError,
)

# Value objects
from src.domain.value_objects.task import (
    AgentSelector,
    Assignment,
    ExecutionSpec,
    HistoryEntry,
    RetryPolicy,
    TaskResult,
)

# Entities
from src.domain.entities.agent import AgentProps

# Aggregates
from src.domain.aggregates.task import TaskAggregate
from src.domain.aggregates.goal import GoalAggregate, GoalStatus, TaskSummary

# Repositories
from src.domain.repositories import AgentRegistryPort, TaskRepositoryPort
from src.domain.repositories.goal_repository import GoalRepositoryPort

# Goal value objects
from src.domain.value_objects.goal import GoalSpec, GoalTaskDef

# Ports
from src.domain.ports import (
    AgentRuntimePort,
    EventPort,
    GitWorkspacePort,
    LeasePort,
    SessionHandle,
    TaskLogsPort,
    TestRunnerPort,
)

# Services
from src.domain.services import (
    ReconciliationAction,
    ReconciliationDecision,
    ReconciliationService,
    SchedulerService,
)

# Rules
from src.domain.rules import TaskRules

# ProjectSpec — aggregate root, repository port, value objects, errors
from src.domain.project_spec import (
    DirectoryRule,
    ForbiddenMutationError,
    ProjectSpec,
    ProjectSpecRepository,
    SpecConstraints,
    SpecNotFoundError,
    SpecObjective,
    SpecValidationError,
    SpecVersion,
    SpecVersionMismatchError,
    StructureSpec,
    TechStack,
)

__all__ = [
    # status
    "TaskStatus", "TrustLevel",
    # events
    "DomainEvent",
    # execution
    "AgentExecutionResult", "ExecutionContext",
    # errors
    "DomainError", "ForbiddenFileEditError",
    "InvalidStatusTransitionError", "MaxRetriesExceededError",
    # value objects — task
    "AgentSelector", "Assignment", "ExecutionSpec",
    "HistoryEntry", "RetryPolicy", "TaskResult",
    # value objects — goal
    "GoalSpec", "GoalTaskDef",
    # entities
    "AgentProps",
    # aggregates
    "TaskAggregate",
    "GoalAggregate", "GoalStatus", "TaskSummary",
    # repositories
    "AgentRegistryPort", "TaskRepositoryPort", "GoalRepositoryPort",
    # ports
    "AgentRuntimePort", "EventPort", "GitWorkspacePort",
    "LeasePort", "SessionHandle", "TaskLogsPort", "TestRunnerPort",
    # services
    "ReconciliationAction", "ReconciliationDecision", "ReconciliationService",
    "SchedulerService",
    # rules
    "TaskRules",
    # project spec — aggregate, repository, value objects, errors
    "ProjectSpec",
    "ProjectSpecRepository",
    "SpecVersion",
    "TechStack",
    "SpecConstraints",
    "StructureSpec",
    "DirectoryRule",
    "SpecObjective",
    "SpecNotFoundError",
    "SpecValidationError",
    "SpecVersionMismatchError",
    "ForbiddenMutationError",
]

# PR value objects
from src.domain.value_objects.pr import (
    PRStatus,
    PRCheckConclusion,
    CheckRunResult,
    PRInfo,
)

# GitHub port
from src.domain.ports.github import GitHubPort, GitHubError, GitHubRateLimitError

# ProjectSpec CI config
from src.domain.project_spec.value_objects import CIConfig

# Roadmap — foundational value object for goal DAG validation
from src.domain.value_objects.goal import Roadmap

# Project state port — planner's persistent memory interface
from src.domain.ports.project_state import ProjectStatePort

# Lease refresher port + factory type (drift fix — app layer no longer imports infra)
from src.domain.ports.lease import LeaseRefresherPort, LeaseRefresherFactory
