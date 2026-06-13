"""
tests/unit/api/test_dependencies.py — DI container/provider resolution.

Verifies the API dependency layer:
  - fails loudly when create_app() has not bound a container
  - static binding (set_container) returns the same container
  - dynamic binding (set_container_provider) re-resolves per request
  - project context resolution surfaces ConfigurationError when unset
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.api import dependencies as deps
from src.infra.settings.models import ConfigurationError


@pytest.fixture(autouse=True)
def reset_provider():
    """Isolate module-level provider state between tests."""
    original = deps._container_provider
    deps._container_provider = None
    yield
    deps._container_provider = original


def test_get_container_raises_before_init():
    with pytest.raises(RuntimeError, match="has not been initialised"):
        deps._get_container()


def test_set_container_binds_static_container():
    container = MagicMock()
    deps.set_container(container)
    assert deps._get_container() is container
    assert deps._get_container() is container  # stable across calls


def test_set_container_provider_resolves_per_request():
    containers = [MagicMock(name="first"), MagicMock(name="second")]
    calls = []

    def provider():
        calls.append(1)
        return containers[min(len(calls), 2) - 1]

    deps.set_container_provider(provider)
    assert deps._get_container() is containers[0]
    assert deps._get_container() is containers[1]
    assert len(calls) == 2


def test_get_project_name_returns_configured_name():
    container = MagicMock()
    container.get_required_project.return_value = "proj-a"
    assert deps.get_project_name(container) == "proj-a"


def test_get_project_name_raises_configuration_error_when_unset():
    container = MagicMock()
    container.get_required_project.side_effect = ConfigurationError(
        "No project configured.\nRun: orchestrator init"
    )
    with pytest.raises(ConfigurationError):
        deps.get_project_name(container)


def test_get_settings_context_returns_container_ctx():
    container = MagicMock()
    assert deps.get_settings_context(container) is container.ctx
