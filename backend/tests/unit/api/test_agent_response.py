"""
tests/unit/api/test_agent_response.py — agent liveness in the read-model.
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.api.routers.agents import _to_response
from src.domain import AgentProps


def test_alive_agent_with_fresh_heartbeat():
    a = AgentProps(
        agent_id="a-1", name="A", capabilities=["code:backend"],
        last_heartbeat=datetime.now(timezone.utc),
    )
    resp = _to_response(a)
    assert resp.alive is True
    assert resp.last_heartbeat is not None


def test_offline_agent_without_heartbeat():
    a = AgentProps(agent_id="a-1", name="A", capabilities=["code:backend"])
    resp = _to_response(a)
    assert resp.alive is False
    assert resp.last_heartbeat is None
