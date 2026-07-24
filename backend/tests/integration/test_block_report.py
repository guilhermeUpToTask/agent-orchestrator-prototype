"""Black-box coverage for the standalone, read-only block frequency report."""

from __future__ import annotations

import ast
import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from src.infra.db.engine import build_engine
from src.infra.db.tables import Base

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
