"""The config-backed RetryPolicy factory: unset keys keep the domain's bare
defaults, and each execution.retry_* key overrides its matching field
independently — plus the end-to-end wire-up that plan creation actually uses
the container's config-derived policy."""

from __future__ import annotations

import pytest

from src.domain.policies.retry_policies import RetryPolicy
from src.infra.container import AppContainer
from src.infra.db.tables import Base
from src.infra.policies.retry_policy_factory import build_retry_policy

pytestmark = pytest.mark.integration


@pytest.fixture
def container(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
    monkeypatch.delenv("ORCHESTRATOR_API_TOKEN", raising=False)
    c = AppContainer(orchestrator_home=tmp_path)
    Base.metadata.create_all(c.engine)
    return c


def test_unset_config_keeps_domain_defaults(container):
    policy = build_retry_policy(container.config_store)
    assert policy == RetryPolicy()


def test_each_key_overrides_its_field_independently(container):
    scope = container.config_store.ORCHESTRATOR_SCOPE
    container.config_store.set(scope, "execution.retry_max_attempts", "20")
    container.config_store.set(scope, "execution.retry_max_backoff_seconds", "3600")

    policy = build_retry_policy(container.config_store)
    assert policy.max_attempts == 20
    assert policy.max_backoff_seconds == 3600.0
    # untouched keys still fall back to the domain defaults
    assert policy.initial_backoff_seconds == RetryPolicy().initial_backoff_seconds
    assert policy.backoff_multiplier == RetryPolicy().backoff_multiplier
    assert policy.jitter_ratio == RetryPolicy().jitter_ratio


def test_all_keys_can_be_overridden(container):
    scope = container.config_store.ORCHESTRATOR_SCOPE
    container.config_store.set(scope, "execution.retry_max_attempts", "5")
    container.config_store.set(scope, "execution.retry_initial_backoff_seconds", "10")
    container.config_store.set(scope, "execution.retry_backoff_multiplier", "3")
    container.config_store.set(scope, "execution.retry_max_backoff_seconds", "120")
    container.config_store.set(scope, "execution.retry_jitter_ratio", "0.1")

    policy = build_retry_policy(container.config_store)
    assert policy.max_attempts == 5
    assert policy.initial_backoff_seconds == 10.0
    assert policy.backoff_multiplier == 3.0
    assert policy.max_backoff_seconds == 120.0
    assert policy.jitter_ratio == 0.1


def test_container_default_retry_policy_rereads_config_live(container):
    """Deliberately NOT cached: a config change must apply to the very next
    read without rebuilding the container (no API restart needed)."""
    assert container.default_retry_policy == RetryPolicy()

    scope = container.config_store.ORCHESTRATOR_SCOPE
    container.config_store.set(scope, "execution.retry_max_attempts", "30")

    assert container.default_retry_policy.max_attempts == 30


def test_created_plan_uses_the_container_configured_retry_policy(container):
    from src.app.use_cases.create_plan import open_project_plan
    from src.domain.entities.project_definition import ProjectDefinition

    scope = container.config_store.ORCHESTRATOR_SCOPE
    container.config_store.set(scope, "execution.retry_max_attempts", "25")
    container.config_store.set(scope, "execution.retry_max_backoff_seconds", "7200")

    project_id = "project-1"
    container.project_repo.add(
        ProjectDefinition(id=project_id, name="Test project", repo_url=None)
    )

    opened = open_project_plan(
        "brief",
        project_id,
        "req-1",
        container.new_unit_of_work(),
        retry_policy=container.default_retry_policy,
    )
    with container.new_unit_of_work() as uow:
        plan = uow.plans.get(opened.plan_id)
    assert plan.retry_policy.max_attempts == 25
    assert plan.retry_policy.max_backoff_seconds == 7200.0


def test_retry_policy_endpoint_partially_updates_an_existing_plan(container):
    """POST /{plan_id}/retry-policy (un-freeze #12) is a DIFFERENT lever from
    execution.retry_* config: config only seeds a NEW plan at creation; this
    endpoint retunes one ALREADY-persisted plan, live, without a replan."""
    from src.api.routers.plans import RetryPolicyUpdateRequest, update_retry_policy_route
    from src.app.use_cases.create_plan import open_project_plan
    from src.domain.entities.project_definition import ProjectDefinition

    project_id = "project-1"
    container.project_repo.add(
        ProjectDefinition(id=project_id, name="Test project", repo_url=None)
    )
    opened = open_project_plan(
        "brief", project_id, "req-1", container.new_unit_of_work()
    )

    update_retry_policy_route(
        opened.plan_id,
        RetryPolicyUpdateRequest(max_attempts=20, max_backoff_seconds=3600),
        container,
    )

    with container.new_unit_of_work() as uow:
        plan = uow.plans.get(opened.plan_id)
    assert plan.retry_policy.max_attempts == 20
    assert plan.retry_policy.max_backoff_seconds == 3600.0
    # untouched fields keep the bare default, not reset by the partial update
    assert plan.retry_policy.backoff_multiplier == RetryPolicy().backoff_multiplier
