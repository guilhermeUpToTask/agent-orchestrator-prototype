import uuid
from typing import Optional

from src.core.models import (
    AgentSelector, DomainEvent, ExecutionSpec, RetryPolicy, TaskAggregate,
)
from src.core.ports import EventPort, TaskRepositoryPort


class TaskCreationService:
    """
    Application service that orchestrates the creation of new tasks.
    It builds the domain aggregate, persists it via the repository,
    and publishes the domain event to trigger the task manager.
    """

    def __init__(self, task_repo: TaskRepositoryPort, event_port: EventPort):
        self._repo = task_repo
        self._events = event_port

    def create_task(
        self,
        title: str,
        description: str,
        capability: str,
        files_allowed_to_modify: list[str],
        feature_id: Optional[str] = None,
        test_command: Optional[str] = None,
        acceptance_criteria: list[str] = None,
        depends_on: list[str] = None,
        max_retries: int = 2,
        min_version: str = ">=1.0.0",
    ) -> TaskAggregate:
        target_feature_id = feature_id or f"feat-{uuid.uuid4().hex[:8]}"
        task_id = f"task-{uuid.uuid4().hex[:12]}"

        # Single quotes in test commands survive the shell but break when
        # PyYAML stores them and bash re-executes. Replace with double quotes.
        safe_test = test_command.replace("'", '"') if test_command else None

        task = TaskAggregate(
            task_id=task_id,
            feature_id=target_feature_id,
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
            ),
            depends_on=depends_on or [],
            retry_policy=RetryPolicy(max_retries=max_retries),
        )

        self._repo.save(task)

        self._events.publish(DomainEvent(
            type="task.created",
            producer="task_creation_service",
            payload={"task_id": task_id},
        ))

        return task
