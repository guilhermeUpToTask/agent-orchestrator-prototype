from datetime import datetime, timezone
from pathlib import Path
from src.domain import AgentProps, TrustLevel
from src.infra.fs.agent_registry import JsonAgentRegistry

def make_agent(agent_id: str = "agent-001") -> AgentProps:
    return AgentProps(
        agent_id=agent_id,
        name=f"Agent {agent_id}",
        capabilities=["backend_dev"],
        version="1.0.0",
        trust_level=TrustLevel.MEDIUM,
    )

class TestJsonAgentRegistry:
    def _make_registry(self, tmp_path: Path):
        return JsonAgentRegistry(tmp_path / "agents" / "registry.json")

    def test_register_and_list(self, tmp_path):
        registry = self._make_registry(tmp_path)
        agent = make_agent("a-001")
        registry.register(agent)
        agents = registry.list_agents()
        assert len(agents) == 1
        assert agents[0].agent_id == "a-001"

    def test_deregister(self, tmp_path):
        registry = self._make_registry(tmp_path)
        agent = make_agent("a-001")
        registry.register(agent)
        registry.deregister("a-001")
        assert registry.list_agents() == []

    def test_get(self, tmp_path):
        registry = self._make_registry(tmp_path)
        agent = make_agent("a-001")
        registry.register(agent)
        result = registry.get("a-001")
        assert result.agent_id == "a-001"
        assert registry.get("nonexistent") is None

    def test_heartbeat(self, tmp_path):
        registry = self._make_registry(tmp_path)
        agent = make_agent("a-001")
        registry.register(agent)
        before = datetime.now(timezone.utc)
        registry.heartbeat("a-001")
        loaded = registry.get("a-001")
        assert loaded.last_heartbeat >= before

    def test_deregister_nonexistent_agent_does_not_raise(self, tmp_path):
        registry = self._make_registry(tmp_path)
        # Should not raise — deregistering a missing agent is a no-op
        registry.deregister("nonexistent-agent")
        assert registry.list_agents() == []

    def test_register_overwrites_existing_agent(self, tmp_path):
        registry = self._make_registry(tmp_path)
        agent = make_agent("a-001")
        registry.register(agent)
        updated = make_agent("a-001")
        updated.version = "2.0.0"
        registry.register(updated)
        loaded = registry.get("a-001")
        assert loaded.version == "2.0.0"
        assert len(registry.list_agents()) == 1
