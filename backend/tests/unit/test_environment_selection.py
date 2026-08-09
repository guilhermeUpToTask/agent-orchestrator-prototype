from __future__ import annotations

from pathlib import Path

import pytest

from agent_orchestrator.infra.container import AppContainer
from agent_orchestrator.infra.db.tables import Base
from agent_orchestrator.infra.environment.container_environment import ContainerEnvironment
from agent_orchestrator.infra.environment.no_environment import NoEnvironment


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """`AppContainer` has no `migrate()`; tests create the schema directly —
    the pattern in test_runtime_regressions.py."""
    Base.metadata.create_all(AppContainer(orchestrator_home=tmp_path).engine)
    return tmp_path


def test_the_default_is_the_permanent_no_environment_fallback(home: Path) -> None:
    assert isinstance(AppContainer(orchestrator_home=home).environment, NoEnvironment)


def test_container_mode_selects_the_container_adapter(home: Path) -> None:
    AppContainer(orchestrator_home=home).config_store.set(
        "orchestrator", "environment.mode", "container"
    )
    # A fresh container: `environment` is a cached_property, so the one that
    # wrote the key would return its already-resolved adapter.
    assert isinstance(
        AppContainer(orchestrator_home=home).environment, ContainerEnvironment
    )


def test_the_configured_binary_reaches_the_adapter(home: Path) -> None:
    """Selecting the adapter and choosing its runtime are separate keys, and
    a mode set without a binary must not silently strand a podman-only host."""
    store = AppContainer(orchestrator_home=home).config_store
    store.set("orchestrator", "environment.mode", "container")
    store.set("orchestrator", "environment.container_binary", "podman")

    environment = AppContainer(orchestrator_home=home).environment
    assert isinstance(environment, ContainerEnvironment)
    assert environment._binary == "podman"


def test_an_unknown_mode_falls_back_rather_than_failing(home: Path) -> None:
    """An acceptance run is advisory: a typo in one config key must not be able
    to take down the promotion or publication gate it was only observing."""
    AppContainer(orchestrator_home=home).config_store.set(
        "orchestrator", "environment.mode", "kubernetes"
    )
    assert isinstance(AppContainer(orchestrator_home=home).environment, NoEnvironment)
