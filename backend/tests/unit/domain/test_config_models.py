"""Unit tests for the config-layer domain models (Phase 1, no I/O)."""
from __future__ import annotations

import pytest

from src.domain.aggregates.task import TaskAggregate
from src.domain.entities.agent_definition import AgentDefinition
from src.domain.entities.model_provider import ModelProvider
from src.domain.entities.project import Project
from src.domain.value_objects.config import (
    ProviderKind,
    RegisteredModel,
    SecretRef,
)
from src.domain.value_objects.task import AgentSelector, ExecutionSpec


class TestSecretRef:
    def test_valid_uri(self) -> None:
        assert SecretRef(uri="secret://provider/anthropic").uri == "secret://provider/anthropic"

    def test_rejects_non_secret_scheme(self) -> None:
        with pytest.raises(ValueError):
            SecretRef(uri="https://example.com/key")

    def test_is_frozen(self) -> None:
        ref = SecretRef(uri="secret://provider/x")
        with pytest.raises(Exception):
            ref.uri = "secret://provider/y"  # type: ignore[misc]

    def test_provider_constructor(self) -> None:
        assert SecretRef.for_provider("openai").uri == "secret://provider/openai"

    def test_project_github_constructor(self) -> None:
        assert SecretRef.for_project_github("p1").uri == "secret://project/p1/github"


class TestModelProvider:
    def _provider(self) -> ModelProvider:
        return ModelProvider(
            id="anthropic",
            kind=ProviderKind.ANTHROPIC,
            secret_ref=SecretRef.for_provider("anthropic"),
        )

    def test_with_model_adds(self) -> None:
        p = self._provider().with_model(
            RegisteredModel(model_id="claude-opus-4-8", display_name="Opus 4.8")
        )
        assert p.has_model("claude-opus-4-8")
        assert len(p.models) == 1

    def test_with_model_replaces_same_id(self) -> None:
        p = self._provider().with_model(
            RegisteredModel(model_id="m", display_name="old")
        ).with_model(
            RegisteredModel(model_id="m", display_name="new")
        )
        assert len(p.models) == 1
        assert p.models[0].display_name == "new"


class TestGlobalScoping:
    """Global entities must not carry a project_id; project-scoped state may."""

    def test_provider_has_no_project_id(self) -> None:
        assert "project_id" not in ModelProvider.model_fields

    def test_agent_definition_has_no_project_id(self) -> None:
        assert "project_id" not in AgentDefinition.model_fields

    def test_project_defaults(self) -> None:
        proj = Project(id="p", name="P", repo_url="git@x:y.git")
        assert proj.default_branch == "main"
        assert proj.github_secret_ref is None
        assert proj.state_version == 0
        assert proj.created_at is not None


class TestTaskProjectId:
    def test_task_aggregate_has_optional_project_id(self) -> None:
        task = TaskAggregate.create(
            title="t",
            description="d",
            execution=ExecutionSpec(type="code"),
            agent_selector=AgentSelector(required_capability="code:backend"),
        )
        assert task.project_id is None

    def test_task_aggregate_accepts_project_id(self) -> None:
        task = TaskAggregate.create(
            title="t",
            description="d",
            execution=ExecutionSpec(type="code"),
            agent_selector=AgentSelector(required_capability="code:backend"),
        )
        task.project_id = "proj-1"
        assert task.project_id == "proj-1"
