"""
tests/unit/test_services.py — Exhaustive tests for domain services.

Covers:
  - SchedulerService.select_agent: eligibility, scoring, edge cases
  - SchedulerService.eligible_agents
  - _is_alive heartbeat logic
  - _satisfies_version: >=, exact match, edge cases
  - LeaseService.should_requeue / should_fail_stale
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from src.core.models import (
    AgentProps,
    AgentSelector,
    Assignment,
    ExecutionSpec,
    RetryPolicy,
    TaskAggregate,
    TaskStatus,
    TrustLevel,
)
from src.core.services import (
    LeaseService,
    SchedulerService,
    _is_alive,
    _parse_version,
    _satisfies_version,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_task(
    required_cap: str = "backend_dev",
    min_version: str = ">=1.0.0",
    status: TaskStatus = TaskStatus.CREATED,
    max_retries: int = 2,
) -> TaskAggregate:
    return TaskAggregate(
        task_id="task-001",
        feature_id="feat-x",
        title="T",
        description="D",
        agent_selector=AgentSelector(
            required_capability=required_cap,
            min_version=min_version,
        ),
        execution=ExecutionSpec(type="code:backend"),
        status=status,
        retry_policy=RetryPolicy(max_retries=max_retries),
    )


def make_agent(
    agent_id: str = "agent-001",
    capabilities: list[str] | None = None,
    version: str = "1.0.0",
    trust: TrustLevel = TrustLevel.MEDIUM,
    active: bool = True,
    heartbeat_age_seconds: float | None = 10.0,  # None = no heartbeat
    max_concurrent: int = 1,
    tools: list[str] | None = None,
) -> AgentProps:
    hb: datetime | None = None
    if heartbeat_age_seconds is not None:
        hb = datetime.now(timezone.utc) - timedelta(seconds=heartbeat_age_seconds)
    return AgentProps(
        agent_id=agent_id,
        name=f"Agent {agent_id}",
        capabilities=capabilities or ["backend_dev"],
        version=version,
        trust_level=trust,
        active=active,
        last_heartbeat=hb,
        max_concurrent_tasks=max_concurrent,
        tools=tools or [],
    )


# ===========================================================================
# Version parsing
# ===========================================================================

class TestParseVersion:

    def test_simple_semver(self):
        assert _parse_version("1.2.3") == (1, 2, 3)

    def test_major_only(self):
        # Only the first 3 numeric groups are used
        assert _parse_version("2.0.0") == (2, 0, 0)

    def test_with_prefix_stripped(self):
        # _satisfies_version strips ">=" before calling _parse_version
        assert _parse_version("1.0.0") == (1, 0, 0)


class TestSatisfiesVersion:

    @pytest.mark.parametrize("agent_ver,constraint,expected", [
        ("1.0.0", ">=1.0.0", True),
        ("1.5.0", ">=1.0.0", True),
        ("2.0.0", ">=1.0.0", True),
        ("0.9.9", ">=1.0.0", False),
        ("1.0.0", ">=2.0.0", False),
        ("2.0.0", ">=2.0.0", True),
        ("1.0.0", "1.0.0", True),   # exact match
        ("1.0.1", "1.0.0", False),  # exact match — different patch
        ("2.0.0", ">=1.99.99", True),
        ("1.10.0", ">=1.9.0", True),  # 10 > 9
    ])
    def test_version_constraints(self, agent_ver, constraint, expected):
        assert _satisfies_version(agent_ver, constraint) == expected


# ===========================================================================
# _is_alive
# ===========================================================================

class TestIsAlive:

    def test_recent_heartbeat_is_alive(self):
        agent = make_agent(heartbeat_age_seconds=5)
        assert _is_alive(agent) is True

    def test_old_heartbeat_is_dead(self):
        agent = make_agent(heartbeat_age_seconds=120)
        assert _is_alive(agent) is False

    def test_exactly_at_threshold_is_dead(self):
        # age == threshold_seconds → NOT alive (strict less-than)
        agent = make_agent(heartbeat_age_seconds=60)
        assert _is_alive(agent, threshold_seconds=60) is False

    def test_just_below_threshold_is_alive(self):
        agent = make_agent(heartbeat_age_seconds=59)
        assert _is_alive(agent, threshold_seconds=60) is True

    def test_no_heartbeat_is_dead(self):
        agent = make_agent(heartbeat_age_seconds=None)
        assert _is_alive(agent) is False

    def test_custom_threshold(self):
        agent = make_agent(heartbeat_age_seconds=100)
        assert _is_alive(agent, threshold_seconds=200) is True
        assert _is_alive(agent, threshold_seconds=50) is False


# ===========================================================================
# SchedulerService.select_agent — eligibility
# ===========================================================================

class TestSchedulerServiceEligibility:
    svc = SchedulerService()

    def test_returns_matching_agent(self):
        task = make_task()
        agent = make_agent()
        result = self.svc.select_agent(task, [agent])
        assert result is not None
        assert result.agent_id == "agent-001"

    def test_returns_none_if_no_agents(self):
        task = make_task()
        assert self.svc.select_agent(task, []) is None

    def test_returns_none_for_wrong_capability(self):
        task = make_task(required_cap="backend_dev")
        agent = make_agent(capabilities=["frontend"])
        assert self.svc.select_agent(task, [agent]) is None

    def test_returns_none_for_inactive_agent(self):
        task = make_task()
        agent = make_agent(active=False)
        assert self.svc.select_agent(task, [agent]) is None

    def test_returns_none_for_dead_agent(self):
        task = make_task()
        agent = make_agent(heartbeat_age_seconds=None)  # no heartbeat
        assert self.svc.select_agent(task, [agent]) is None

    def test_returns_none_for_old_heartbeat(self):
        task = make_task()
        agent = make_agent(heartbeat_age_seconds=300)
        assert self.svc.select_agent(task, [agent]) is None

    def test_returns_none_for_version_mismatch(self):
        task = make_task(min_version=">=2.0.0")
        agent = make_agent(version="1.9.9")
        assert self.svc.select_agent(task, [agent]) is None

    def test_returns_agent_matching_exact_version(self):
        task = make_task(min_version="1.5.0")
        agent = make_agent(version="1.5.0")
        result = self.svc.select_agent(task, [agent])
        assert result is not None

    def test_filters_out_agents_missing_capability(self):
        task = make_task(required_cap="ml")
        agents = [
            make_agent("a1", capabilities=["backend_dev"]),
            make_agent("a2", capabilities=["ml"]),
        ]
        result = self.svc.select_agent(task, agents)
        assert result.agent_id == "a2"


# ===========================================================================
# SchedulerService.select_agent — scoring
# ===========================================================================

class TestSchedulerServiceScoring:
    svc = SchedulerService()

    def test_prefers_high_trust_over_low(self):
        task = make_task()
        low = make_agent("low", trust=TrustLevel.LOW)
        high = make_agent("high", trust=TrustLevel.HIGH)
        result = self.svc.select_agent(task, [low, high])
        assert result.agent_id == "high"

    def test_prefers_high_trust_over_medium(self):
        task = make_task()
        med = make_agent("med", trust=TrustLevel.MEDIUM)
        high = make_agent("high", trust=TrustLevel.HIGH)
        result = self.svc.select_agent(task, [med, high])
        assert result.agent_id == "high"

    def test_prefers_medium_trust_over_low(self):
        task = make_task()
        low = make_agent("low", trust=TrustLevel.LOW)
        med = make_agent("med", trust=TrustLevel.MEDIUM)
        result = self.svc.select_agent(task, [low, med])
        assert result.agent_id == "med"

    def test_tiebreak_by_max_concurrent_tasks(self):
        task = make_task()
        a1 = make_agent("a1", trust=TrustLevel.HIGH, max_concurrent=1)
        a2 = make_agent("a2", trust=TrustLevel.HIGH, max_concurrent=3)
        result = self.svc.select_agent(task, [a1, a2])
        assert result.agent_id == "a2"

    def test_tiebreak_by_tool_count(self):
        task = make_task()
        a1 = make_agent("a1", trust=TrustLevel.MEDIUM, max_concurrent=1, tools=[])
        a2 = make_agent("a2", trust=TrustLevel.MEDIUM, max_concurrent=1, tools=["git", "pytest"])
        result = self.svc.select_agent(task, [a1, a2])
        assert result.agent_id == "a2"

    def test_trust_level_dominates_tools(self):
        """Low trust with 100 tools should lose to high trust with 0 tools."""
        task = make_task()
        low_many_tools = make_agent("low", trust=TrustLevel.LOW, tools=[f"t{i}" for i in range(100)])
        high_no_tools = make_agent("high", trust=TrustLevel.HIGH, tools=[])
        result = self.svc.select_agent(task, [low_many_tools, high_no_tools])
        assert result.agent_id == "high"

    def test_single_eligible_agent_returned(self):
        task = make_task()
        agent = make_agent()
        result = self.svc.select_agent(task, [agent])
        assert result.agent_id == agent.agent_id


# ===========================================================================
# SchedulerService.eligible_agents
# ===========================================================================

class TestEligibleAgents:
    svc = SchedulerService()

    def test_returns_all_eligible(self):
        task = make_task()
        agents = [make_agent("a1"), make_agent("a2"), make_agent("a3")]
        eligible = self.svc.eligible_agents(task, agents)
        assert len(eligible) == 3

    def test_excludes_inactive(self):
        task = make_task()
        agents = [make_agent("a1"), make_agent("a2", active=False)]
        eligible = self.svc.eligible_agents(task, agents)
        assert len(eligible) == 1
        assert eligible[0].agent_id == "a1"

    def test_excludes_dead(self):
        task = make_task()
        agents = [make_agent("a1"), make_agent("a2", heartbeat_age_seconds=None)]
        eligible = self.svc.eligible_agents(task, agents)
        assert len(eligible) == 1

    def test_returns_empty_if_none_eligible(self):
        task = make_task(required_cap="ml")
        agents = [make_agent("a1", capabilities=["backend_dev"])]
        assert self.svc.eligible_agents(task, agents) == []


# ===========================================================================
# LeaseService
# ===========================================================================

class TestLeaseService:

    # should_requeue -----------------------------------------------------------

    def test_should_requeue_assigned_expired_lease(self):
        task = make_task(status=TaskStatus.ASSIGNED)
        task.assignment = Assignment(agent_id="a")
        assert LeaseService.should_requeue(task, lease_active=False) is True

    def test_should_not_requeue_active_lease(self):
        task = make_task(status=TaskStatus.ASSIGNED)
        task.assignment = Assignment(agent_id="a")
        assert LeaseService.should_requeue(task, lease_active=True) is False

    def test_should_not_requeue_non_assigned_status(self):
        for status in [TaskStatus.CREATED, TaskStatus.IN_PROGRESS, TaskStatus.SUCCEEDED]:
            task = make_task(status=status)
            assert LeaseService.should_requeue(task, lease_active=False) is False

    def test_should_not_requeue_if_retries_exhausted(self):
        task = make_task(status=TaskStatus.ASSIGNED, max_retries=2)
        task.retry_policy.attempt = 2
        task.assignment = Assignment(agent_id="a")
        assert LeaseService.should_requeue(task, lease_active=False) is False

    def test_should_requeue_at_attempt_less_than_max(self):
        task = make_task(status=TaskStatus.ASSIGNED, max_retries=3)
        task.retry_policy.attempt = 2  # 2 < 3
        task.assignment = Assignment(agent_id="a")
        assert LeaseService.should_requeue(task, lease_active=False) is True

    # should_fail_stale --------------------------------------------------------

    def test_should_fail_stale_in_progress_expired(self):
        task = make_task(status=TaskStatus.IN_PROGRESS)
        assert LeaseService.should_fail_stale(task, lease_active=False) is True

    def test_should_not_fail_stale_active_lease(self):
        task = make_task(status=TaskStatus.IN_PROGRESS)
        assert LeaseService.should_fail_stale(task, lease_active=True) is False

    def test_should_not_fail_stale_non_in_progress_status(self):
        for status in [TaskStatus.CREATED, TaskStatus.ASSIGNED, TaskStatus.FAILED]:
            task = make_task(status=status)
            assert LeaseService.should_fail_stale(task, lease_active=False) is False