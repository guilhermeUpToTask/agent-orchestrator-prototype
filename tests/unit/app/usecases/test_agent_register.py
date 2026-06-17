"""
tests/unit/app/usecases/test_agent_register.py — capability validation on
agent registration.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.app.usecases.agent_register import AgentRegisterUseCase
from src.domain import AgentProps, UnknownCapabilityError


def _agent(caps: list[str]) -> AgentProps:
    return AgentProps(agent_id="a-1", name="A", capabilities=caps)


def test_rejects_unregistered_capability():
    caps = MagicMock()
    caps.exists.return_value = False
    caps.list_tags.return_value = ["code:backend"]
    uc = AgentRegisterUseCase(agent_registry=MagicMock(), capability_registry=caps)

    with pytest.raises(UnknownCapabilityError):
        uc.execute(_agent(["code:frontend"]))


def test_registers_when_all_capabilities_known():
    registry = MagicMock()
    caps = MagicMock()
    caps.exists.return_value = True
    uc = AgentRegisterUseCase(agent_registry=registry, capability_registry=caps)

    uc.execute(_agent(["code:backend"]))

    registry.register.assert_called_once()


def test_skips_validation_when_no_capability_registry():
    registry = MagicMock()
    uc = AgentRegisterUseCase(agent_registry=registry)  # no capability registry
    uc.execute(_agent(["anything"]))
    registry.register.assert_called_once()
