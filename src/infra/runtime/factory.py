"""
src/infra/runtime/factory.py — Agent runtime adapter factory.

Resolves an AgentProps runtime_type to the correct AgentRuntimePort adapter.
API keys are read from SettingsService (env only — SecretSettings).
No adapter reads os.environ or config files directly.

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
from src.infra.settings import SettingsService
from src.infra.logging import LoggingRuntimeWrapper


def build_agent_runtime(agent_props: AgentProps) -> AgentRuntimePort:
    """
    Resolve runtime_type → AgentRuntimePort adapter.

    API keys come from SecretSettings (env-only).  No adapter reads os.environ
    or config files directly — all secrets are passed in explicitly.
    All runtimes are wrapped with LoggingRuntimeWrapper for live observability.
    """
    ctx = SettingsService().load()
    secrets = ctx.secrets

    if ctx.machine.mode == "dry-run" or agent_props.runtime_type == "dry-run":
        from src.infra.runtime.dry_run_runtime import SimulatedAgentRuntime
        base_runtime = SimulatedAgentRuntime()
    else:
        def _build_gemini(cfg: dict) -> AgentRuntimePort:
            from src.infra.runtime.gemini_runtime import GeminiAgentRuntime
            return GeminiAgentRuntime(
                api_key=secrets.gemini_api_key,
                model=cfg.get("model", GeminiAgentRuntime.DEFAULT_MODEL),
                extra_flags=cfg.get("extra_flags", []),
            )

        def _build_claude(cfg: dict) -> AgentRuntimePort:
            from src.infra.runtime.claude_code_runtime import ClaudeCodeRuntime
            return ClaudeCodeRuntime(
                api_key=secrets.anthropic_api_key,
                model=cfg.get("model", ClaudeCodeRuntime.DEFAULT_MODEL),
                extra_flags=cfg.get("extra_flags", []),
            )

        def _build_pi(cfg: dict) -> AgentRuntimePort:
            from src.infra.runtime.pi_runtime import PiAgentRuntime
            backend = cfg.get("backend", "openrouter")
            if backend == "gemini":
                api_key = secrets.gemini_api_key
            elif backend == "openrouter":
                api_key = secrets.openrouter_api_key
            else:
                api_key = secrets.anthropic_api_key
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

    # Wrap with logging — derive logs_dir from ProjectPaths
    from src.infra.project_paths import ProjectPaths
    json_log_dir = str(ProjectPaths.for_project(
        ctx.machine.orchestrator_home, ctx.machine.project_name
    ).logs_dir)
    return LoggingRuntimeWrapper(
        base_runtime=base_runtime,
        agent_name=agent_props.runtime_type,
        json_log_dir=json_log_dir,
    )


def build_runtime_factory() -> Callable[[AgentProps], AgentRuntimePort]:
    """Return a callable that maps AgentProps → AgentRuntimePort."""
    return build_agent_runtime
