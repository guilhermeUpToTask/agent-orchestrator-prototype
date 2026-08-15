"""A caller-supplied `limit` cannot ask for the whole table.

Phase 10A: `list_planning_artifacts` and `agent_events` declared a bare
`limit: int`, which reaches `LIMIT :limit` verbatim. SQLite reads a NEGATIVE
limit as "no limit", so `?limit=-1` returned every row the plan had — 750 of
750 in the reproduction — while `tail_lines` in the same module was already
bounded `ge=0, le=2000`.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from praxis_orchestrator.api import dependencies
from praxis_orchestrator.api.server import create_app
from praxis_orchestrator.infra.container import AppContainer
from praxis_orchestrator.infra.db.agent_event_sink import _INSERT_SQL
from praxis_orchestrator.infra.db.tables import Base

pytestmark = pytest.mark.integration

ROWS = 250
DEFAULT_AGENT_EVENT_LIMIT = 200


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PRAXIS_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("PRAXIS_API_TOKEN", raising=False)
    container = AppContainer(orchestrator_home=tmp_path)
    Base.metadata.create_all(container.engine)
    with TestClient(create_app(container)) as test_client:
        test_client.container = container  # type: ignore[attr-defined]
        yield test_client
    dependencies.set_container(None)  # type: ignore[arg-type]


@pytest.fixture
def plan_with_events(client):
    project = client.post("/api/projects", json={"name": "bounds", "repo_url": None})
    plan = client.post(
        "/api/plans", json={"brief": "bounds", "project_id": project.json()["id"]}
    )
    plan_id = plan.json()["plan_id"]

    container = client.container  # type: ignore[attr-defined]
    with container.session_factory() as session:
        for i in range(ROWS):
            session.execute(
                _INSERT_SQL,
                {
                    "event_id": f"e{i}",
                    "plan_id": plan_id,
                    "goal_id": None,
                    "task_id": "t1",
                    "run_id": "r1",
                    "attempt_id": "a1",
                    "attempt": 1,
                    "seq": i,
                    "type": "probe",
                    "observation_kind": "probe",
                    "source_sequence": i,
                    "payload": "{}",
                    "occurred_at": "2026-08-10T00:00:00Z",
                    "recorded_at": "2026-08-10T00:00:00Z",
                },
            )
        session.commit()
    return plan_id


@pytest.mark.parametrize("limit", [-1, 0, 1001, 10**9])
def test_agent_events_refuses_an_out_of_range_limit(client, plan_with_events, limit):
    response = client.get(
        f"/api/plans/{plan_with_events}/agent-events", params={"limit": limit}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_agent_events_caps_at_its_default_without_a_limit(client, plan_with_events):
    """The regression: `limit=-1` used to return all of them."""
    response = client.get(f"/api/plans/{plan_with_events}/agent-events")

    assert response.status_code == 200
    assert len(response.json()) == DEFAULT_AGENT_EVENT_LIMIT


def test_a_limit_inside_the_range_is_honoured(client, plan_with_events):
    response = client.get(
        f"/api/plans/{plan_with_events}/agent-events", params={"limit": 5}
    )

    assert response.status_code == 200
    assert len(response.json()) == 5


@pytest.mark.parametrize("limit", [-1, 0, 201])
def test_planning_artifacts_refuses_an_out_of_range_limit(client, plan_with_events, limit):
    response = client.get(
        f"/api/plans/{plan_with_events}/planning-artifacts", params={"limit": limit}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_planning_artifacts_without_a_goal_id_spans_every_goal(client, plan_with_events) -> None:
    """Omitting `goal_id` must mean EVERY goal, not "the plan-wide ones".

    The store matches `goal_id IS :goal_id`, so a NULL selected only plan-wide
    rows. A goal-scoped purpose — `verification_baseline` is written per goal —
    therefore answered `[]` while rows existed, and an operator reading that
    empty list would reasonably conclude the baseline was never recorded
    (Phase 10B).
    """
    from datetime import datetime, timezone

    from praxis_orchestrator.app.ports import PlanningArtifact

    container = client.container  # type: ignore[attr-defined]
    for index, goal_id in enumerate(("goal-a", "goal-b")):
        container.planning_artifacts.append(
            PlanningArtifact(
                plan_id=plan_with_events,
                goal_id=goal_id,
                purpose="verification_baseline",
                operation_id=f"op-{index}",
                sequence=0,
                input_fingerprint=f"task-{index}:1",
                outcome="committed",
                created_at=datetime(2026, 8, 11, tzinfo=timezone.utc),
                payload={"verdict": "red"},
            )
        )

    everything = client.get(
        f"/api/plans/{plan_with_events}/planning-artifacts",
        params={"purpose": "verification_baseline"},
    )
    assert everything.status_code == 200
    assert len(everything.json()) == 2, "both goals' baselines must be reachable"

    scoped = client.get(
        f"/api/plans/{plan_with_events}/planning-artifacts",
        params={"purpose": "verification_baseline", "goal_id": "goal-a"},
    )
    assert scoped.status_code == 200
    assert len(scoped.json()) == 1, "naming a goal still scopes to that goal"
