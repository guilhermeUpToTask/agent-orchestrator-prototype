"""
src/infra/runtime/factory.py — Agent runtime adapter factory.

API keys are loaded via SecretSettings.require_*() — fails fast with a
clear message if a key is missing rather than passing an empty string to
the runtime and getting a cryptic auth error later.
"""

from __future__ import annotations
from typing import Callable
from src.domain import AgentProps
from src.domain.ports import AgentRuntimePort
from src.infra.settings import SettingsContext
from src.infra.logging import LoggingRuntimeWrapper


def build_agent_runtime(agent_props: AgentProps, ctx: SettingsContext) -> AgentRuntimePort:
    secrets = ctx.secrets

    if ctx.machine.mode == "dry-run" or agent_props.runtime_type == "dry-run":
        from src.infra.runtime.dry_run_runtime import SimulatedAgentRuntime

        base_runtime = SimulatedAgentRuntime()
    else:

        def _build_gemini(cfg: dict) -> AgentRuntimePort:
            from src.infra.runtime.gemini_runtime import GeminiAgentRuntime

            return GeminiAgentRuntime(
                api_key=secrets.require_gemini_key(),
                model=cfg.get("model", GeminiAgentRuntime.DEFAULT_MODEL),
                extra_flags=cfg.get("extra_flags", []),
            )

        def _build_claude(cfg: dict) -> AgentRuntimePort:
            from src.infra.runtime.claude_code_runtime import ClaudeCodeRuntime

            return ClaudeCodeRuntime(
                api_key=secrets.require_anthropic_key(),
                model=cfg.get("model", ClaudeCodeRuntime.DEFAULT_MODEL),
                extra_flags=cfg.get("extra_flags", []),
            )

        def _build_pi(cfg: dict) -> AgentRuntimePort:
            from src.infra.runtime.pi_runtime import PiAgentRuntime

            backend = cfg.get("backend", "openrouter")
            if backend == "gemini":
                api_key = secrets.require_gemini_key()
            elif backend == "openrouter":
                api_key = secrets.require_openrouter_key()
            else:
                api_key = secrets.require_anthropic_key()
            return PiAgentRuntime(
                api_key=api_key,
                model=cfg.get("model", PiAgentRuntime.DEFAULT_MODEL),
                extra_flags=cfg.get("extra_flags", []),
                backend=backend,
            )

        _builders: dict[str, Callable[[dict], AgentRuntimePort]] = {
            "gemini": _build_gemini,
            "claude": _build_claude,
            "pi": _build_pi,
        }
        builder = _builders.get(agent_props.runtime_type)
        if not builder:
            valid = ", ".join(sorted(_builders.keys()) + ["dry-run"])
            raise ValueError(
                f"Unknown runtime_type '{agent_props.runtime_type}' for agent "
                f"'{agent_props.agent_id}'. Valid values: {valid}"
            )
        base_runtime = builder(agent_props.runtime_config)

    from src.infra.project_paths import ProjectPaths

    json_log_dir = str(
        ProjectPaths.for_project(ctx.machine.orchestrator_home, ctx.machine.project_name).logs_dir
    )
    return LoggingRuntimeWrapper(
        base_runtime=base_runtime,
        agent_name=agent_props.runtime_type,
        json_log_dir=json_log_dir,
    )


def build_runtime_factory(ctx: SettingsContext) -> Callable[[AgentProps], AgentRuntimePort]:
    return lambda props: build_agent_runtime(props, ctx)
