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

# Repositories
from src.domain.repositories import AgentRegistryPort, TaskRepositoryPort

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
    # value objects
    "AgentSelector", "Assignment", "ExecutionSpec",
    "HistoryEntry", "RetryPolicy", "TaskResult",
    # entities
    "AgentProps",
    # aggregates
    "TaskAggregate",
    # repositories
    "AgentRegistryPort", "TaskRepositoryPort",
    # ports
    "AgentRuntimePort", "EventPort", "GitWorkspacePort",
    "LeasePort", "SessionHandle", "TaskLogsPort", "TestRunnerPort",
    # services
    "ReconciliationAction", "ReconciliationDecision", "ReconciliationService",
    "SchedulerService",
    # rules
    "TaskRules",
]
