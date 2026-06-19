"""
tests/unit/infra/runtime/planners/test_planner_factory.py

The planner factory resolves provider/model/base_url/key entirely from project
config and fails fast when provider or model is unset. Every provider is built
on the single OpenAI-compatible runtime — no vendor SDK, no default model.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.infra.runtime.planners.openai_interactive_planner_runtime import (
    OpenAIInteractivePlannerRuntime,
)
from src.infra.runtime.planners.openai_planner_runtime import OpenAIPlannerRuntime
from src.infra.runtime.planners.planner_factory import (
    build_autonomous_planner,
    build_interactive_planner,
)
from src.infra.settings import ConfigurationError, SettingsService


def _ctx(tmp_path: Path, **kwargs):
    return SettingsService.for_testing(orchestrator_home=tmp_path, mode="real", **kwargs)


class TestProviderResolution:
    def test_openai_default_endpoint(self, tmp_path):
        ctx = _ctx(tmp_path, planner_provider="openai", planner_model="gpt-4o", openai_api_key="k")
        runtime = build_autonomous_planner(ctx)
        assert isinstance(runtime, OpenAIPlannerRuntime)
        assert runtime.model == "gpt-4o"

    def test_anthropic_reachable_via_openai_compat(self, tmp_path):
        # Claude stays reachable through the OpenAI-compatible endpoint with no
        # anthropic SDK — the preset supplies Anthropic's /v1 base_url.
        ctx = _ctx(
            tmp_path,
            planner_provider="anthropic",
            planner_model="claude-sonnet-4-6",
            anthropic_api_key="sk-ant",
        )
        runtime = build_autonomous_planner(ctx)
        assert isinstance(runtime, OpenAIPlannerRuntime)
        assert runtime._runtime._adapter._client.base_url.host == "api.anthropic.com"

    def test_explicit_base_url_overrides_preset(self, tmp_path):
        ctx = _ctx(
            tmp_path,
            planner_provider="openrouter",
            planner_model="x/y",
            planner_base_url="https://example.test/v1",
            openrouter_api_key="k",
        )
        runtime = build_autonomous_planner(ctx)
        assert runtime._runtime._adapter._client.base_url.host == "example.test"

    def test_interactive_runtime_built(self, tmp_path):
        ctx = _ctx(tmp_path, planner_provider="openai", planner_model="gpt-4o", openai_api_key="k")
        assert isinstance(build_interactive_planner(ctx), OpenAIInteractivePlannerRuntime)


class TestFailFast:
    def test_missing_provider(self, tmp_path):
        ctx = _ctx(tmp_path, planner_model="gpt-4o", openai_api_key="k")
        with pytest.raises(ConfigurationError, match="planner_provider"):
            build_autonomous_planner(ctx)

    def test_missing_model(self, tmp_path):
        ctx = _ctx(tmp_path, planner_provider="openai", openai_api_key="k")
        with pytest.raises(ConfigurationError, match="planner_model"):
            build_autonomous_planner(ctx)

    def test_unknown_provider(self, tmp_path):
        ctx = _ctx(tmp_path, planner_provider="bogus", planner_model="m", openai_api_key="k")
        with pytest.raises(ConfigurationError, match="Unknown planner_provider"):
            build_autonomous_planner(ctx)

    def test_missing_key(self, tmp_path):
        ctx = _ctx(tmp_path, planner_provider="openai", planner_model="gpt-4o")
        with pytest.raises(ConfigurationError, match="OPENAI_API_KEY"):
            build_autonomous_planner(ctx)

    def test_local_requires_base_url(self, tmp_path):
        ctx = _ctx(tmp_path, planner_provider="local", planner_model="m", openai_api_key="k")
        with pytest.raises(ConfigurationError, match="planner_base_url"):
            build_autonomous_planner(ctx)
