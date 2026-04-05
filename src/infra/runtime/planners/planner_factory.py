"""
src/infra/runtime/planner_factory.py — Strategy pattern for Planner runtimes.
"""

from abc import ABC, abstractmethod
from typing import Dict, Type

from src.domain.ports.planner import PlannerRuntimePort
from src.infra.settings import SettingsContext


class PlannerProviderStrategy(ABC):
    @abstractmethod
    def build_autonomous(self, ctx: SettingsContext) -> PlannerRuntimePort:
        pass

    @abstractmethod
    def build_interactive(self, ctx: SettingsContext) -> PlannerRuntimePort:
        pass


class AnthropicPlannerStrategy(PlannerProviderStrategy):
    def build_autonomous(self, ctx: SettingsContext) -> PlannerRuntimePort:
        from src.infra.runtime.planners.anthropic_planner_runtime import AnthropicPlannerRuntime

        return AnthropicPlannerRuntime(
            api_key=ctx.secrets.require_anthropic_key(),
            model=ctx.project.planner_model,
        )

    def build_interactive(self, ctx: SettingsContext) -> PlannerRuntimePort:
        from src.infra.runtime.planners.anthropic_interactive_planner_runtime import (
            AnthropicInteractivePlannerRuntime,
        )

        return AnthropicInteractivePlannerRuntime(
            api_key=ctx.secrets.require_anthropic_key(),
            model=ctx.project.planner_model,
        )


class OpenAIPlannerStrategy(PlannerProviderStrategy):
    def build_autonomous(self, ctx: SettingsContext) -> PlannerRuntimePort:
        from src.infra.runtime.planners.openai_planner_runtime import OpenAIPlannerRuntime

        # Note: We still read the OpenAI API key from env here since you
        # haven't added it to SecretSettings yet, but you easily could!
        import os

        return OpenAIPlannerRuntime(
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            model=ctx.project.planner_model,
        )

    def build_interactive(self, ctx: SettingsContext) -> PlannerRuntimePort:
        from src.infra.runtime.planners.openai_interactive_planner_runtime import (
            OpenAIInteractivePlannerRuntime,
        )
        import os

        return OpenAIInteractivePlannerRuntime(
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            model=ctx.project.planner_model,
        )


class OpenRouterPlannerStrategy(PlannerProviderStrategy):
    def build_autonomous(self, ctx: SettingsContext) -> PlannerRuntimePort:
        from src.infra.runtime.planners.openai_planner_runtime import OpenAIPlannerRuntime

        return OpenAIPlannerRuntime(
            api_key=ctx.secrets.require_openrouter_key(),
            model=ctx.project.planner_model,
            base_url="https://openrouter.ai/api/v1",
        )

    def build_interactive(self, ctx: SettingsContext) -> PlannerRuntimePort:
        from src.infra.runtime.planners.openai_interactive_planner_runtime import (
            OpenAIInteractivePlannerRuntime,
        )

        return OpenAIInteractivePlannerRuntime(
            api_key=ctx.secrets.require_openrouter_key(),
            model=ctx.project.planner_model,
            base_url="https://openrouter.ai/api/v1",
        )


_STRATEGIES: Dict[str, Type[PlannerProviderStrategy]] = {
    "anthropic": AnthropicPlannerStrategy,
    "openai": OpenAIPlannerStrategy,
    "openrouter": OpenRouterPlannerStrategy,
}


def get_planner_strategy(ctx: SettingsContext) -> PlannerProviderStrategy:
    """Resolve the active planner strategy based on ProjectSettings."""
    provider = ctx.project.planner_provider.lower()

    strategy_cls = _STRATEGIES.get(provider)
    if not strategy_cls:
        valid = ", ".join(_STRATEGIES.keys())
        raise ValueError(
            f"Unknown planner_provider '{provider}' in project.json. Valid options are: {valid}"
        )

    return strategy_cls()
