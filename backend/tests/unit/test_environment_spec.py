from __future__ import annotations

from praxis_orchestrator.infra.environment.spec import (
    CONTAINER_BINARY_KEY,
    read_container_binary,
)


class _Store:
    def __init__(self, values: dict[tuple[str, str], str]) -> None:
        self._values = values

    def get(self, scope: str, key: str) -> str | None:
        return self._values.get((scope, key))


def test_container_binary_defaults_to_docker() -> None:
    assert read_container_binary(_Store({})) == "docker"


def test_container_binary_is_configuration() -> None:
    store = _Store({("orchestrator", CONTAINER_BINARY_KEY): "podman"})
    assert read_container_binary(store) == "podman"


def test_blank_container_binary_degrades_to_the_default() -> None:
    store = _Store({("orchestrator", CONTAINER_BINARY_KEY): "   "})
    assert read_container_binary(store) == "docker"
