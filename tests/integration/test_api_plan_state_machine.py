"""
tests/integration/test_api_plan_state_machine.py — Planning API vs state machine.

Drives the real FastAPI app with a dry-run AppContainer whose in-memory
plan repository is seeded into specific ProjectPlanStatus states, then
verifies that /plan/* endpoints enforce the lifecycle:

  discovery → architecture → phase_active → phase_review → done

Invalid sequences must yield 409 Conflict with a structured body that
reports the attempted action, current status, and expected statuses.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from src.api.server import create_app
from src.domain.aggregates.project_plan import (
    Phase,
    PhaseStatus,
    ProjectBrief,
    ProjectPlan,
)
from src.domain.project_spec.aggregate import ProjectSpec
from src.infra.container import AppContainer
from src.infra.fs.project_spec_repository import FileProjectSpecRepository

_PROJECT = "plan-sm-test"


@pytest.fixture()
def container(tmp_path, monkeypatch):
    """Dry-run container scoped to an isolated orchestrator home with a spec."""
    home = tmp_path / ".orchestrator"
    home.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))
    monkeypatch.setenv("AGENT_MODE", "dry-run")
    (home / "config.json").write_text(
        json.dumps({"project_name": _PROJECT}), encoding="utf-8"
    )

    spec = ProjectSpec.create(
        name=_PROJECT,
        objective_description="Plan state machine integration test",
        objective_domain="testing",
        backend=["python"],
        database=["redis"],
        infra=["docker"],
        forbidden=[],
        required=[],
        directories=[{"name": "src", "purpose": "Code"}],
        version="0.1.0",
    )
    FileProjectSpecRepository(orchestrator_home=home).save(spec)

    return AppContainer.from_env()


@pytest.fixture()
def client(container):
    return TestClient(create_app(container))


def _brief() -> ProjectBrief:
    return ProjectBrief(
        vision="Test vision",
        constraints=["constraint"],
        phase_1_exit_criteria="phase 1 done",
        open_questions=[],
    )


def _phase(index: int = 0) -> Phase:
    return Phase(
        index=index,
        name=f"Phase {index}",
        goal="ship it",
        goal_names=[],
        status=PhaseStatus.PLANNED,
        lessons="",
        exit_criteria="tests pass",
    )


def _seed(container, plan: ProjectPlan) -> ProjectPlan:
    container.project_plan_repo.save(plan)
    return plan


def _assert_conflict(resp, *, action: str, current: str, expected: list[str]) -> None:
    assert resp.status_code == 409
    body = resp.json()
    assert body["action"] == action
    assert body["current_status"] == current
    assert body["expected_status"] == expected
    assert current in body["detail"]


# ---------------------------------------------------------------------------
# Invalid sequences → structured 409
# ---------------------------------------------------------------------------

def test_approve_architecture_during_discovery_conflicts(container, client):
    _seed(container, ProjectPlan.create("v"))
    resp = client.post("/api/plan/approve-architecture", json={"decision_ids": []})
    _assert_conflict(
        resp,
        action="approve architecture",
        current="discovery",
        expected=["architecture"],
    )


def test_approve_phase_during_architecture_conflicts(container, client):
    plan = ProjectPlan.create("v").approve_brief(_brief())
    _seed(container, plan)
    resp = client.post("/api/plan/approve-phase", json={"approve_next": True})
    _assert_conflict(
        resp,
        action="approve phase review",
        current="architecture",
        expected=["phase_review"],
    )


def test_approve_brief_twice_conflicts(container, client):
    plan = ProjectPlan.create("v").model_copy(update={"brief": _brief()})
    _seed(container, plan)

    first = client.post("/api/plan/approve-brief")
    assert first.status_code == 200
    assert first.json()["plan_status"] == "architecture"

    second = client.post("/api/plan/approve-brief")
    _assert_conflict(
        second,
        action="approve brief",
        current="architecture",
        expected=["discovery"],
    )


def test_approve_architecture_during_phase_review_conflicts(container, client):
    plan = (
        ProjectPlan.create("v")
        .approve_brief(_brief())
        .approve_phase([_phase()])
        .trigger_review()
    )
    _seed(container, plan)
    resp = client.post("/api/plan/approve-architecture", json={"decision_ids": []})
    _assert_conflict(
        resp,
        action="approve architecture",
        current="phase_review",
        expected=["architecture"],
    )


def test_approve_brief_without_brief_conflicts(container, client):
    _seed(container, ProjectPlan.create("v"))  # discovery, no brief yet
    resp = client.post("/api/plan/approve-brief")
    assert resp.status_code == 409
    assert "No brief to approve" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Read model reflects the seeded state machine position
# ---------------------------------------------------------------------------

def test_get_plan_reports_current_status(container, client):
    plan = ProjectPlan.create("v").approve_brief(_brief()).approve_phase([_phase()])
    _seed(container, plan)
    body = client.get("/api/plan").json()
    assert body["status"] == "phase_active"
    assert body["current_phase_index"] == 0
    assert body["phases"][0]["status"] == "active"


def test_plan_history_records_transitions(container, client):
    plan = ProjectPlan.create("v").approve_brief(_brief()).approve_phase([_phase()])
    _seed(container, plan)
    events = [h["event"] for h in client.get("/api/plan/history").json()]
    assert "project_plan.brief_approved" in events
    assert "project_plan.phase_approved" in events
