"""
src/infra/runtime/factory.py — Agent runtime adapter factory.

Moved here from src/infra/factory.py (Phase 8 refactoring).
A factory within a factory is a smell: the main factory.py was responsible
for wiring application-layer components AND knowing how to construct every
runtime adapter — two different concerns.

This module owns the single responsibility of resolving an AgentProps
runtime_type to the correct AgentRuntimePort adapter, reading API keys
from OrchestratorConfig.

Public surface:
  build_agent_runtime(agent_props)  → AgentRuntimePort
  build_runtime_factory()           → Callable[[AgentProps], AgentRuntimePort]

runtime_type values:
  "dry-run" — SimulatedAgentRuntime (CI/tests, always available)
  "gemini"  — GeminiAgentRuntime    (requires GEMINI_API_KEY)
  "claude"  — ClaudeCodeRuntime     (requires ANTHROPIC_API_KEY)
  "pi"      — PiAgentRuntime        (requires key depending on backend)
"""
from __future__ import annotations

from typing import Callable

from src.domain import AgentProps
from src.domain.ports import AgentRuntimePort
from src.infra.config import config as app_config
from src.infra.logging import LoggingRuntimeWrapper


def build_agent_runtime(agent_props: AgentProps) -> AgentRuntimePort:
    """
    Resolve runtime_type → AgentRuntimePort adapter.

    API keys are read from OrchestratorConfig (env vars / .env / config.json).
    No adapter reads os.environ directly.

    All runtimes are wrapped with LoggingRuntimeWrapper for live observability.
    """
    # Build the base runtime
    if app_config.mode == "dry-run" or agent_props.runtime_type == "dry-run":
        from src.infra.runtime.dry_run_runtime import SimulatedAgentRuntime
        base_runtime = SimulatedAgentRuntime()
    else:
        def _build_gemini(cfg: dict) -> AgentRuntimePort:
            from src.infra.runtime.gemini_runtime import GeminiAgentRuntime
            return GeminiAgentRuntime(
                api_key=app_config.gemini_api_key.get_secret_value(),
                model=cfg.get("model", GeminiAgentRuntime.DEFAULT_MODEL),
                extra_flags=cfg.get("extra_flags", []),
            )

        def _build_claude(cfg: dict) -> AgentRuntimePort:
            from src.infra.runtime.claude_code_runtime import ClaudeCodeRuntime
            return ClaudeCodeRuntime(
                api_key=app_config.anthropic_api_key.get_secret_value(),
                model=cfg.get("model", ClaudeCodeRuntime.DEFAULT_MODEL),
                extra_flags=cfg.get("extra_flags", []),
            )

        def _build_pi(cfg: dict) -> AgentRuntimePort:
            from src.infra.runtime.pi_runtime import PiAgentRuntime

            backend = cfg.get("backend", "openrouter")
            if backend == "gemini":
                api_key = app_config.gemini_api_key.get_secret_value()
            elif backend == "openrouter":
                api_key = app_config.openrouter_api_key.get_secret_value()
            else:
                api_key = app_config.anthropic_api_key.get_secret_value()

            return PiAgentRuntime(
                api_key=api_key,
                model=cfg.get("model", PiAgentRuntime.DEFAULT_MODEL),
                extra_flags=cfg.get("extra_flags", []),
                backend=backend,
            )

        _builders: dict[str, Callable[[dict], AgentRuntimePort]] = {
            "gemini": _build_gemini,
            "claude": _build_claude,
            "pi":     _build_pi,
        }

        builder = _builders.get(agent_props.runtime_type)
        if not builder:
            valid = ", ".join(sorted(_builders.keys()) + ["dry-run"])
            raise ValueError(
                f"Unknown runtime_type '{agent_props.runtime_type}' for agent "
                f"'{agent_props.agent_id}'. Valid values: {valid}"
            )
        base_runtime = builder(agent_props.runtime_config)

    # Wrap with logging for live observability
    # Use logs_dir from config for JSON log storage
    json_log_dir = str(app_config.logs_dir) if hasattr(app_config, 'logs_dir') else None
    logged_runtime = LoggingRuntimeWrapper(
        base_runtime=base_runtime,
        agent_name=agent_props.runtime_type,
        json_log_dir=json_log_dir,
    )

    return logged_runtime


def build_runtime_factory() -> Callable[[AgentProps], AgentRuntimePort]:
    """
    Return a callable that maps AgentProps → AgentRuntimePort.

    All returned runtimes are wrapped with LoggingRuntimeWrapper for live
    observability. Passed to TaskExecuteUseCase so it can build the correct
    runtime per-task after the assigned agent is known.
    """
    return build_agent_runtime
