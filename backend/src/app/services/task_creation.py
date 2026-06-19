from typing import Any, Optional

import structlog

from src.domain import AgentSelector, DomainEvent, ExecutionSpec, TaskAggregate
from src.domain import CapabilityRegistryPort, EventPort, TaskRepositoryPort
from src.domain import normalize_capability

log = structlog.get_logger(__name__)

# Fallback when a task is created with an unregistered/malformed capability.
# Task creation is largely LLM-driven (JIT planner), so we coerce-and-warn
# rather than hard-fail a whole goal on a single bad tag.
_DEFAULT_CAPABILITY = "code:backend"


class TaskCreationService:
    """
    Application service that orchestrates the creation of new tasks.
    It builds the domain aggregate, persists it via the repository,
    and publishes the domain event to trigger the task manager.
    """

    def __init__(
        self,
        task_repo: TaskRepositoryPort,
        event_port: EventPort,
        capability_registry: Optional[CapabilityRegistryPort] = None,
    ):
        self._repo = task_repo
        self._events = event_port
        self._capabilities = capability_registry

    def _resolve_capability(self, capability: str) -> str:
        """Normalize + validate against the registry, coercing unknowns.

        Keeps the planner resilient: a malformed or unregistered capability is
        mapped to the default with a warning instead of crashing goal planning.
        """
        try:
            tag = normalize_capability(capability)
        except ValueError:
            log.warning("task_creation.capability_malformed", raw=capability, fallback=_DEFAULT_CAPABILITY)
            return _DEFAULT_CAPABILITY
        if self._capabilities is not None and not self._capabilities.exists(tag):
            log.warning("task_creation.capability_unregistered", tag=tag, fallback=_DEFAULT_CAPABILITY)
            return _DEFAULT_CAPABILITY
        return tag

    def create_task(
        self,
        title: str,
        description: str,
        capability: str,
        files_allowed_to_modify: list[str],
        feature_id: Optional[str] = None,
        test_command: Optional[str] = None,
        acceptance_criteria: Optional[list[str]] = None,
        depends_on: Optional[list[str]] = None,
        max_retries: int = 2,
        min_version: str = ">=1.0.0",
        task_id: Optional[str] = None,
        constraints: Optional[dict[str, Any]] = None,
    ) -> TaskAggregate:
        # Single quotes in test commands survive the shell but break when
        # PyYAML stores them and bash re-executes. Replace with double quotes.
        safe_test = test_command.replace("'", '"') if test_command else None

        capability = self._resolve_capability(capability)

        task = TaskAggregate.create(
            title=title,
            description=description,
            agent_selector=AgentSelector(
                required_capability=capability,
                min_version=min_version,
            ),
            execution=ExecutionSpec(
                type=capability,
                files_allowed_to_modify=files_allowed_to_modify,
                test_command=safe_test,
                acceptance_criteria=acceptance_criteria or [],
                constraints=constraints or {},
            ),
            feature_id=feature_id,
            depends_on=depends_on,
            max_retries=max_retries,
            task_id=task_id,
        )

        self._repo.save(task)

        self._events.publish(DomainEvent(
            type="task.created",
            producer="task_creation_service",
            payload={"task_id": task.task_id},
        ))

        return task
