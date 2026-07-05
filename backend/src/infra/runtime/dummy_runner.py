"""
src/infra/runtime/dummy_runner.py — the agent_runner.mode=dry-run runtime.

The scriptable DummyAgentRunner IS the dry-run runtime: it implements the same
AgentRunner port and raises TaskFailed with the same shared FailureKind
taxonomy as the real CLI runners, so dry-run flows exercise exactly the
retry/terminal paths production hits.

The implementation lives in src/app/testing/fakes.py (the application layer may
not import infra, so the sharing points this way); this module is the infra
name the container wires.
"""
from __future__ import annotations

from src.app.testing.fakes import DummyAgentRunner, DummyBehavior

__all__ = ["DummyAgentRunner", "DummyBehavior"]
