"""
tests/unit/infra/runtime/test_require_runtime_config.py

Agents must declare model (+ backend for pi) in their runtime_config; there are
no silent defaults, so a misconfigured agent fails fast.
"""
from __future__ import annotations

import pytest

from src.domain import AgentProps
from src.infra.runtime.factory import require_runtime_config
from src.infra.settings import ConfigurationError


def _agent(runtime_type: str, runtime_config: dict) -> AgentProps:
    return AgentProps(
        agent_id="a-1", name="A", capabilities=["code:backend"],
        runtime_type=runtime_type, runtime_config=runtime_config,
    )


def test_missing_model_rejected():
    with pytest.raises(ConfigurationError, match="model"):
        require_runtime_config(_agent("gemini", {}))


def test_pi_missing_backend_rejected():
    with pytest.raises(ConfigurationError, match="backend"):
        require_runtime_config(_agent("pi", {"model": "claude-sonnet-4-5"}))


def test_pi_with_model_and_backend_ok():
    require_runtime_config(_agent("pi", {"model": "claude-sonnet-4-5", "backend": "anthropic"}))


def test_gemini_with_model_ok():
    require_runtime_config(_agent("gemini", {"model": "gemini-2.0-flash"}))


def test_dry_run_is_exempt():
    require_runtime_config(_agent("dry-run", {}))  # no raise
