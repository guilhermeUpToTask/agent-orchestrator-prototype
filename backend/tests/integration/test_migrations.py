"""The fresh Alembic chain must produce the same schema the ORM metadata
declares (the test harness uses metadata.create_all; production uses
`alembic upgrade head` — they must never drift)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from src.infra.db.tables import Base

BACKEND_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.integration


def _columns(engine, table: str) -> dict[str, bool]:
    """name -> nullable, for drift comparison."""
    return {c["name"]: bool(c["nullable"]) for c in inspect(engine).get_columns(table)}


def test_alembic_upgrade_head_matches_metadata(tmp_path):
    # migrate one db
    migrated_url = f"sqlite:///{tmp_path / 'migrated.db'}"
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", migrated_url)
    command.upgrade(cfg, "head")

    # create_all another
    created_url = f"sqlite:///{tmp_path / 'created.db'}"
    created_engine = create_engine(created_url)
    Base.metadata.create_all(created_engine)

    migrated_engine = create_engine(migrated_url)
    migrated_tables = set(inspect(migrated_engine).get_table_names()) - {"alembic_version"}
    created_tables = set(inspect(created_engine).get_table_names())
    assert migrated_tables == created_tables

    for table in sorted(created_tables):
        assert _columns(migrated_engine, table) == _columns(created_engine, table), (
            f"schema drift in table {table!r}"
        )


def test_upgrade_from_0006_preserves_rows_and_adds_execution_ledger(tmp_path):
    url = f"sqlite:///{tmp_path / 'predecessor.db'}"
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "0006_pause_and_telemetry")

    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO plans "
                "(id, version, phase, iteration, data, paused, created_at, updated_at) "
                "VALUES ('p1', 1, 'running', 1, '{}', 0, :now, :now)"
            ),
            {"now": "2026-07-13T00:00:00+00:00"},
        )
        connection.execute(
            text(
                "INSERT INTO outbox "
                "(event_id, plan_id, type, payload, occurred_at) "
                "VALUES ('e1', 'p1', 'TaskStarted', '{}', :now)"
            ),
            {"now": "2026-07-13T00:00:00+00:00"},
        )
        connection.execute(
            text(
                "INSERT INTO agent_events "
                "(event_id, plan_id, task_id, attempt, seq, type, payload, occurred_at) "
                "VALUES ('a1', 'p1', NULL, 0, 0, 'llm.call', '{}', :now)"
            ),
            {"now": "2026-07-13T00:00:00+00:00"},
        )

    command.upgrade(cfg, "head")

    assert {"execution_runs", "execution_attempts"}.issubset(inspect(engine).get_table_names())
    with engine.begin() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM plans")).scalar_one() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM outbox")).scalar_one() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM agent_events")).scalar_one() == 1
        connection.execute(
            text(
                "INSERT INTO execution_runs "
                "(id, plan_id, goal_id, task_id, status, started_at) "
                "VALUES ('r1', 'p1', 'g1', 't1', 'running', :now)"
            ),
            {"now": "2026-07-13T00:00:01+00:00"},
        )
        connection.execute(
            text(
                "INSERT INTO execution_attempts "
                "(id, run_id, plan_id, goal_id, task_id, number, task_attempt, "
                "status, started_at) "
                "VALUES ('x1', 'r1', 'p1', 'g1', 't1', 1, 1, 'running', :now)"
            ),
            {"now": "2026-07-13T00:00:01+00:00"},
        )
        assert (
            connection.execute(
                text("SELECT run_id FROM execution_attempts WHERE id = 'x1'")
            ).scalar_one()
            == "r1"
        )


def test_upgrade_from_0007_backfills_typed_observation_metadata(tmp_path):
    url = f"sqlite:///{tmp_path / 'observations-predecessor.db'}"
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "0007_execution_ledger")

    engine = create_engine(url)
    now = "2026-07-13T00:00:00+00:00"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO agent_events "
                "(event_id, plan_id, task_id, attempt, seq, type, payload, occurred_at) "
                "VALUES ('legacy-1', 'p1', NULL, 0, 4, 'llm.call', '{}', :now)"
            ),
            {"now": now},
        )

    command.upgrade(cfg, "head")

    expected = {
        "goal_id",
        "run_id",
        "attempt_id",
        "observation_kind",
        "source",
        "quality",
        "schema_version",
        "source_sequence",
        "recorded_at",
    }
    assert expected.issubset(_columns(engine, "agent_events"))
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT observation_kind, source, quality, schema_version, "
                "source_sequence, recorded_at FROM agent_events "
                "WHERE event_id = 'legacy-1'"
            )
        ).one()
    assert tuple(row) == (
        "llm.call",
        "legacy",
        "legacy_unknown",
        0,
        4,
        now,
    )


def test_cyclic_migration_maps_legacy_phases_then_quarantines_unbound_rows(tmp_path):
    url = f"sqlite:///{tmp_path / 'legacy-lifecycle.db'}"
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "0008_typed_observations")

    expected = {
        "done": "idle",
        "failed": "blocked",
        "running": "running",
        "review": "waiting",
        "awaiting_review": "waiting",
        "enriching": "running",
        "architecture": "running",
        "discovery": "waiting",
        "replanning": "waiting",
    }
    engine = create_engine(url)
    now = "2026-07-13T00:00:00+00:00"
    with engine.begin() as connection:
        for index, phase in enumerate(expected):
            plan_id = f"legacy-{phase}"
            data = {
                "id": plan_id,
                "brief": "preserved",
                "version": index,
                "phase": phase,
                "goals": [],
            }
            connection.execute(
                text(
                    "INSERT INTO plans "
                    "(id, version, phase, iteration, data, paused, created_at, updated_at) "
                    "VALUES (:id, :version, :phase, 1, :data, 0, :now, :now)"
                ),
                {
                    "id": plan_id,
                    "version": index,
                    "phase": phase,
                    "data": json.dumps(data),
                    "now": now,
                },
            )

    command.upgrade(cfg, "head")

    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT id, status, project_id, data FROM plans ORDER BY id")
        ).all()
    assert len(rows) == len(expected)
    for plan_id, status, project_id, raw_data in rows:
        phase = str(plan_id).removeprefix("legacy-")
        data = json.loads(raw_data)
        assert status == "blocked"
        assert project_id is None
        assert data["legacy_phase"] == phase
        assert data["legacy_mapped_status"] == expected[phase]
        assert data["status"] == "blocked"
        assert data["brief"] == "preserved"
        assert data["block"]["kind"] == "project_binding"
        assert data["block"]["legal_resolutions"] == ["bind_project"]


def test_operational_recovery_migration_backfills_open_attempt_liveness(tmp_path):
    url = f"sqlite:///{tmp_path / 'operational-recovery.db'}"
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "0009_cyclic_project_plan")

    engine = create_engine(url)
    now = "2026-07-15T00:00:00+00:00"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO projects (id, name, repo_url) VALUES ('project-1', 'project-1', NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO plans "
                "(id, project_id, version, phase, status, iteration, data, paused, "
                "pause_requested, created_at, updated_at) "
                "VALUES ('p1', 'project-1', 1, 'running', 'running', 1, '{}', 0, 0, :now, :now)"
            ),
            {"now": now},
        )
        connection.execute(
            text(
                "INSERT INTO execution_runs "
                "(id, plan_id, goal_id, task_id, status, started_at) "
                "VALUES ('run-1', 'p1', 'g1', 't1', 'running', :now)"
            ),
            {"now": now},
        )
        connection.execute(
            text(
                "INSERT INTO execution_attempts "
                "(id, run_id, plan_id, goal_id, task_id, number, task_attempt, status, started_at) "
                "VALUES ('attempt-1', 'run-1', 'p1', 'g1', 't1', 1, 1, 'running', :now)"
            ),
            {"now": now},
        )

    command.upgrade(cfg, "head")

    tables = set(inspect(engine).get_table_names())
    assert {"planning_operations", "runtime_circuits"}.issubset(tables)
    assert {
        "last_liveness_at",
        "runtime",
        "provider_id",
        "model_id",
        "failure_kind",
        "retry_at",
        "safe_message",
    }.issubset(_columns(engine, "execution_attempts"))
    with engine.connect() as connection:
        liveness = connection.execute(
            text("SELECT last_liveness_at FROM execution_attempts WHERE id = 'attempt-1'")
        ).scalar_one()
    assert liveness == now
