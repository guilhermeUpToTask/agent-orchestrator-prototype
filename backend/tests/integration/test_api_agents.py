"""
tests/integration/test_api_agents.py — agent registry CRUD.

Covers the edit/delete operations that complete the registry CRUD: PUT
replaces an existing agent (path id authoritative), DELETE removes it, and
both 404 when the agent is unknown.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from src.api.server import create_app
from src.domain import AgentProps


def _client(container) -> TestClient:
    return TestClient(create_app(container=container))


def _agent(agent_id: str = "a1") -> AgentProps:
    return AgentProps(
        agent_id=agent_id,
        name="Worker",
        capabilities=["code"],
        version="1.0.0",
        trust_level="medium",
        active=True,
        max_concurrent_tasks=2,
        runtime_type="gemini",
    )


def _body() -> dict:
    return {
        "agent_id": "ignored-in-body",
        "name": "Worker v2",
        "capabilities": ["code", "test"],
        "version": "1.1.0",
        "trust_level": "high",
        "active": True,
        "max_concurrent_tasks": 3,
        "runtime_type": "gemini",
    }


class TestUpdateAgent:
    def test_put_updates_existing_agent(self):
        container = MagicMock()
        registry = container.agent_registry
        registry.get.return_value = _agent("a1")

        with _client(container) as client:
            r = client.put("/api/agents/a1", json=_body())
            assert r.status_code == 200
            assert registry.register.called
            # path id wins over the body id
            saved = registry.register.call_args.args[0]
            assert saved.agent_id == "a1"
            assert saved.name == "Worker v2"

    def test_put_unknown_agent_is_404(self):
        container = MagicMock()
        container.agent_registry.get.return_value = None
        with _client(container) as client:
            r = client.put("/api/agents/nope", json=_body())
            assert r.status_code == 404
            assert not container.agent_registry.register.called


class TestDeleteAgent:
    def test_delete_existing_agent_returns_204(self):
        container = MagicMock()
        registry = container.agent_registry
        registry.get.return_value = _agent("a1")
        with _client(container) as client:
            r = client.delete("/api/agents/a1")
            assert r.status_code == 204
            registry.deregister.assert_called_once_with("a1")

    def test_delete_unknown_agent_is_404(self):
        container = MagicMock()
        container.agent_registry.get.return_value = None
        with _client(container) as client:
            r = client.delete("/api/agents/nope")
            assert r.status_code == 404
            assert not container.agent_registry.deregister.called
