"""Black-box coverage for the standalone, read-only block frequency report,
plus (below) coverage for the `requires_human` projection served on every
block in `GET /api/plans/{id}` -- unrelated to the standalone script, but
placed here per the implementation plan for phase 4.2."""

from __future__ import annotations

import ast
import hashlib
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from agent_orchestrator.api import dependencies
from agent_orchestrator.api.server import create_app
from agent_orchestrator.app import block_policy
from agent_orchestrator.domain.aggregates.planner_orchestrator import Plan, PlanPhase
from agent_orchestrator.domain.entities.planning_artifacts import PlanBlock, PlanStatus
from agent_orchestrator.domain.entities.project_definition import ProjectDefinition
from agent_orchestrator.infra.container import AppContainer
from agent_orchestrator.infra.db.engine import build_engine
from agent_orchestrator.infra.db.tables import Base

pytestmark = pytest.mark.integration

T0 = "2026-07-16T10:00:00+00:00"
T1 = "2026-07-16T10:01:00+00:00"
SCRIPT = Path(__file__).parents[2] / "scripts" / "block_report.py"


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _seed_database(path: Path) -> None:
    """Minimal plan fixture: one active goal block + one resolved plan-wide block."""
    engine = build_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()

    plan = {
        "id": "p1",
        "project_id": "project-1",
        "brief": "measure block frequency",
        "status": "blocked",
        "phase": "running",
        "version": 3,
        "iteration": 1,
        "cycles": [],
        "block": {
            "id": "block-plan-1",
            "kind": "provider_capacity",
            "explanation": "quota exhausted (later resolved)",
            "stage": "implementation",
            "goal_id": None,
            "task_id": "t-shared",
            "legal_resolutions": ["wait_and_retry"],
            "created_at": T0,
            "resolved_at": T1,
            "resolution": "wait_and_retry",
        },
        "goal_blocks": {
            "g1": {
                "id": "block-goal-1",
                "kind": "verification_failed",
                "explanation": "tests still red",
                "stage": "verification",
                "goal_id": "g1",
                "task_id": "t-shared",
                "legal_resolutions": ["retry_stage", "edit_task"],
                "created_at": T1,
                "resolved_at": None,
            }
        },
    }
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO projects (id, name, repo_url) VALUES (?, ?, NULL)",
            ("project-1", "Project 1"),
        )
        connection.execute(
            """
            INSERT INTO plans
                (id, project_id, status, version, phase, iteration, data,
                 claimed_by, claimed_at, lease_expires_at, lease_seconds,
                 retry_not_before, paused, pause_requested, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, 0, 0, ?, ?)
            """,
            (
                "p1",
                "project-1",
                "blocked",
                3,
                "running",
                1,
                json.dumps(plan),
                T0,
                T1,
            ),
        )


def test_block_report_is_standalone_and_has_no_system_imports():
    source = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_roots = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_roots.update(
        str(node.module).split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )

    assert "src" not in imported_roots
    assert "sqlalchemy" not in imported_roots
    assert "mode=ro" in source
    assert "PRAGMA query_only = ON" in source


def test_active_goal_block_and_resolved_plan_block_counts(tmp_path: Path) -> None:
    database = tmp_path / "orchestrator.db"
    _seed_database(database)
    before = _hash(database)

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(database), "--pretty"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert _hash(database) == before
    report = json.loads(completed.stdout)
    assert report["schema_version"] == "1.0"
    assert report["source"]["read_only"] is True
    assert report["totals"] == {
        "plans_scanned": 1,
        "blocks": 2,
        "active": 1,
        "resolved": 1,
    }
    by_key = {(row["kind"], row["stage"]): row for row in report["by_kind_stage"]}
    assert by_key[("provider_capacity", "implementation")] == {
        "kind": "provider_capacity",
        "stage": "implementation",
        "total": 1,
        "active": 0,
        "resolved": 1,
    }
    assert by_key[("verification_failed", "verification")] == {
        "kind": "verification_failed",
        "stage": "verification",
        "total": 1,
        "active": 1,
        "resolved": 0,
    }
    assert report["per_plan"] == [
        {
            "plan_id": "p1",
            "total": 2,
            "active": 1,
            "resolved": 1,
        }
    ]
    assert report["task_repeat_offenders"] == [
        {
            "task_id": "t-shared",
            "count": 2,
            "plan_ids": ["p1"],
        }
    ]


def test_rejects_unmigrated_database_with_actionable_error(tmp_path: Path) -> None:
    database = tmp_path / "empty.db"
    sqlite3.connect(database).close()

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(database)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "database schema is missing required tables; run migrations first" in completed.stderr
    assert completed.stdout == ""


# ---- `requires_human` served on every block (P4.2 Task 4) ----
#
# `src/app/block_policy.py` is the single source of the "is this my problem, or
# does the orchestrator recover on its own?" verdict. Nothing served it before
# this projection -- these tests iterate the policy table itself (never a
# hard-coded kind list), so a newly added kind cannot ship unserved.

NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("ORCHESTRATOR_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("ORCHESTRATOR_API_TOKEN", raising=False)
    container = AppContainer(orchestrator_home=tmp_path)
    Base.metadata.create_all(container.engine)
    container.project_repo.add(
        ProjectDefinition(id="project-1", name="Test project", repo_url=None)
    )
    app = create_app(container)
    with TestClient(app) as test_client:
        yield test_client
    dependencies.set_container(None)  # type: ignore[arg-type]


def _save(plan: Plan) -> None:
    container = dependencies.get_container()
    with container.new_unit_of_work() as uow:
        uow.plans.save(plan)


@pytest.mark.parametrize("policy", block_policy._POLICIES, ids=lambda p: p.kind)
def test_plan_wide_block_reports_requires_human_over_http(
    client: TestClient, policy: block_policy.BlockPolicy
) -> None:
    plan = Plan(
        id="plan-wide-block",
        brief="requires_human projection",
        project_id="project-1",
        phase=PlanPhase.RUNNING,
        status=PlanStatus.BLOCKED,
        block=PlanBlock(
            id="block-plan-wide",
            kind=policy.kind,
            explanation=f"{policy.kind} needs attention",
            stage="implementation",
            legal_resolutions=block_policy.resolutions_for(policy.kind),
            created_at=NOW,
        ),
    )
    _save(plan)

    detail = client.get(f"/api/plans/{plan.id}").json()

    assert detail["block"]["kind"] == policy.kind
    assert detail["block"]["requires_human"] == policy.requires_human
    assert detail["block"]["requires_human"] == block_policy.requires_human(policy.kind)


@pytest.mark.parametrize("policy", block_policy._POLICIES, ids=lambda p: p.kind)
def test_goal_block_reports_requires_human_over_http(
    client: TestClient, policy: block_policy.BlockPolicy
) -> None:
    plan = Plan(
        id="goal-block",
        brief="requires_human projection",
        project_id="project-1",
        phase=PlanPhase.RUNNING,
        status=PlanStatus.BLOCKED,
        goal_blocks={
            "g1": PlanBlock(
                id="block-goal-1",
                kind=policy.kind,
                explanation=f"{policy.kind} needs attention",
                stage="implementation",
                goal_id="g1",
                legal_resolutions=block_policy.resolutions_for(policy.kind),
                created_at=NOW,
            )
        },
    )
    _save(plan)

    detail = client.get(f"/api/plans/{plan.id}").json()

    goal_block = detail["goal_blocks"]["g1"]
    assert goal_block["kind"] == policy.kind
    assert goal_block["requires_human"] == policy.requires_human
    assert goal_block["requires_human"] == block_policy.requires_human(policy.kind)
