"""
tests/unit/infra/test_container.py
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.infra.container import AppContainer
from src.infra.settings import SettingsService
from src.infra.settings.models import ConfigurationError


def _write_dummy_spec(tmp_path: Path):
    """Eagerly loaded Use Cases require a valid project_spec to exist."""
    project_dir = tmp_path / "projects" / "container-test"
    project_dir.mkdir(parents=True, exist_ok=True)
    spec_data = """
meta:
  name: container-test
  version: 0.1.0
objective:
  description: test
  domain: test
"""
    (project_dir / "project_spec.yaml").write_text(spec_data)


@pytest.fixture
def dry_run_ctx(tmp_path):
    _write_dummy_spec(tmp_path)
    return SettingsService.for_testing(
        orchestrator_home=tmp_path,
        project_name="container-test",
        mode="dry-run",
        github_token="fake-token",
        anthropic_api_key="fake-ant-key",
    )


@pytest.fixture
def real_mode_ctx(tmp_path):
    _write_dummy_spec(tmp_path)
    return SettingsService.for_testing(
        orchestrator_home=tmp_path,
        project_name="container-test",
        mode="real",
        redis_url="redis://localhost:6379/0",
        github_token="fake-token",
        anthropic_api_key="fake-ant-key",
    )


class TestAppContainerWiring:
    def test_container_resolves_all_use_cases_in_dry_run(self, dry_run_ctx):
        app = AppContainer(dry_run_ctx)

        assert app.task_execute_usecase is not None
        assert app.task_assign_usecase is not None
        assert app.planner_orchestrator is not None
        assert app.task_graph_orchestrator is not None
        assert app.project_reset_usecase is not None
        assert app.goal_init_usecase is not None
        assert app.advance_goal_from_pr_usecase is not None

        from src.infra.redis_adapters.event_adapter import InMemoryEventAdapter

        assert isinstance(app.event_port, InMemoryEventAdapter)

    @patch("redis.from_url")
    def test_container_resolves_all_use_cases_in_real_mode(self, mock_redis, real_mode_ctx):
        app = AppContainer(real_mode_ctx)

        assert app.event_port is not None
        assert app.lease_port is not None
        assert app.telemetry_emitter is not None

        mock_redis.assert_called_once_with(real_mode_ctx.machine.redis_url, decode_responses=False)

        from src.infra.redis_adapters.event_adapter import RedisEventAdapter

        assert isinstance(app.event_port, RedisEventAdapter)

    def test_cached_properties_return_singletons(self, dry_run_ctx):
        app = AppContainer(dry_run_ctx)

        repo1 = app.task_repo
        repo2 = app.task_repo
        assert repo1 is repo2

        assert app.task_execute_usecase._task_repo is app.task_repo

    def test_get_required_project_raises_when_missing(self, tmp_path):
        ctx = SettingsService.for_testing(
            orchestrator_home=tmp_path,
            project_name=None,
        )
        app = AppContainer(ctx)

        with pytest.raises(ConfigurationError, match="No project configured"):
            app.get_required_project()

    def test_dynamic_handler_generation(self, dry_run_ctx):
        app = AppContainer(dry_run_ctx)
        handler = app.get_worker_handler(agent_id="test-agent-99")
        assert handler is not None
        assert handler._agent_id == "test-agent-99"

    def test_github_client_fails_fast_when_token_missing_in_real_mode(self, tmp_path):
        _write_dummy_spec(tmp_path)
        ctx = SettingsService.for_testing(
            orchestrator_home=tmp_path,
            project_name="container-test",
            mode="real",
            github_token="",  # Intentionally missing
        )
        app = AppContainer(ctx)

        # In real mode, GitHubClient instantiation raises ConfigurationError
        # when GITHUB_TOKEN is omitted, rather than falling back gracefully.
        with pytest.raises(ConfigurationError, match="GITHUB_TOKEN is not set"):
            _ = app.github_client
