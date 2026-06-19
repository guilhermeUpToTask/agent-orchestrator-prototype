"""
src/infra/runtime/planners/planner_factory.py — Provider-neutral planner builder.

A single OpenAI-SDK runtime reaches every provider through an
OpenAI-compatible endpoint (OpenAI, OpenRouter, Anthropic, Gemini, or any
local/self-hosted server). There is no vendor-specific SDK and no default
model: the provider, model, and (optional) base_url all come from project
config, and resolution fails fast when provider or model is unset.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.domain.ports.planner import PlannerRuntimePort
from src.infra.runtime.planners.openai_interactive_planner_runtime import (
    OpenAIInteractivePlannerRuntime,
)
from src.infra.runtime.planners.openai_planner_runtime import OpenAIPlannerRuntime
from src.infra.settings import ConfigurationError, SettingsContext


@dataclass(frozen=True)
class ProviderPreset:
    """Per-provider defaults: the OpenAI-compatible endpoint and key env var."""

    default_base_url: Optional[str]
    api_key_env: str


# Each provider is just an OpenAI-compatible endpoint + which env var holds its
# key. `local` has no default endpoint — it requires an explicit planner_base_url.
_PRESETS: dict[str, ProviderPreset] = {
    "openai": ProviderPreset(default_base_url=None, api_key_env="OPENAI_API_KEY"),
    "openrouter": ProviderPreset(
        default_base_url="https://openrouter.ai/api/v1", api_key_env="OPENROUTER_API_KEY"
    ),
    "anthropic": ProviderPreset(
        default_base_url="https://api.anthropic.com/v1/", api_key_env="ANTHROPIC_API_KEY"
    ),
    "gemini": ProviderPreset(
        default_base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        api_key_env="GEMINI_API_KEY",
    ),
    "local": ProviderPreset(default_base_url=None, api_key_env="OPENAI_API_KEY"),
}


@dataclass(frozen=True)
class _ResolvedPlannerConfig:
    api_key: str
    model: str
    base_url: Optional[str]


def _resolve(ctx: SettingsContext) -> _ResolvedPlannerConfig:
    """Resolve provider/model/base_url/key from project config — fail fast."""
    provider = (ctx.project.planner_provider or "").strip().lower()
    if not provider:
        raise ConfigurationError(
            "planner_provider is not set in project.json.\n"
            f"Set one of: {', '.join(_PRESETS)} (or run `orchestrate init`)."
        )

    preset = _PRESETS.get(provider)
    if preset is None:
        raise ConfigurationError(
            f"Unknown planner_provider '{provider}' in project.json. "
            f"Valid options are: {', '.join(_PRESETS)}."
        )

    model = (ctx.project.planner_model or "").strip()
    if not model:
        raise ConfigurationError(
            "planner_model is not set in project.json.\n"
            "The planning layer carries no default model — set planner_model explicitly."
        )

    base_url = ctx.project.planner_base_url or preset.default_base_url
    if provider == "local" and not base_url:
        raise ConfigurationError(
            "planner_provider 'local' requires planner_base_url in project.json "
            "(the OpenAI-compatible endpoint of your local/self-hosted server)."
        )

    api_key = ctx.secrets.require_planner_key(preset.api_key_env)
    return _ResolvedPlannerConfig(api_key=api_key, model=model, base_url=base_url)


def build_autonomous_planner(ctx: SettingsContext) -> PlannerRuntimePort:
    """Build the autonomous planner runtime from project config."""
    cfg = _resolve(ctx)
    return OpenAIPlannerRuntime(api_key=cfg.api_key, model=cfg.model, base_url=cfg.base_url)


def build_interactive_planner(ctx: SettingsContext) -> PlannerRuntimePort:
    """Build the interactive (discovery) planner runtime from project config."""
    cfg = _resolve(ctx)
    return OpenAIInteractivePlannerRuntime(
        api_key=cfg.api_key, model=cfg.model, base_url=cfg.base_url
    )
