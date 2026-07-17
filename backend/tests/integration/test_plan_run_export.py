"""Black-box coverage for the standalone, read-only plan-run exporter."""

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
T2 = "2026-07-16T10:02:00+00:00"
SCRIPT = Path(__file__).parents[2] / "scripts" / "export_plan_runs.py"
SNAPSHOT_SCRIPT = Path(__file__).parents[2] / "scripts" / "snapshot_current_plan.py"


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _seed_database(path: Path) -> None:
    engine = build_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()

    p1 = {
        "id": "p1",
        "project_id": "project-1",
        "brief": "ship a verified API",
        "status": "blocked",
        "phase": "running",
        "version": 7,
        "iteration": 1,
        "cycles": [
            {
                "id": "cycle-1",
                "status": "active",
                "goals": [
                    {
                        "id": "g1",
                        "status": "running",
                        "tasks": [
                            {
                                "id": "t1",
                                "status": "done",
                                "agent_id": "agent-1",
                                "role_agent_ids": {"implementer": "agent-1"},
                            },
                            {"id": "t2", "status": "failed", "agent_id": "agent-1"},
                        ],
                    }
                ],
            }
        ],
        "block": {
            "kind": "provider_capacity",
            "stage": "implementation",
            "resolved_at": None,
            "evidence_refs": ["runtime-circuit://pi/provider/model"],
        },
    }
    p2 = {
        "id": "p2",
        "project_id": "project-2",
        "brief": "idle plan",
        "status": "idle",
        "phase": "discovery",
        "version": 1,
        "iteration": 1,
        "cycles": [],
        "goals": [],
    }
    with sqlite3.connect(path) as connection:
        connection.executemany(
            "INSERT INTO projects (id, name, repo_url) VALUES (?, ?, NULL)",
            (("project-1", "Project 1"), ("project-2", "Project 2")),
        )
        connection.executemany(
            "INSERT INTO providers (id, name, base_url, api_key_ref) VALUES (?, ?, ?, ?)",
            (
                (
                    "provider",
                    "Referenced Provider",
                    "https://internal.example.test/v1",
                    "secret://referenced-provider-key",
                ),
                (
                    "unused-provider",
                    "Unused Provider",
                    "https://unused.example.test/v1",
                    "secret://unused-provider-key",
                ),
            ),
        )
        connection.executemany(
            "INSERT INTO models (id, provider_id, name) VALUES (?, ?, ?)",
            (
                ("model", "provider", "Referenced Model"),
                ("unused-model", "unused-provider", "Unused Model"),
            ),
        )
        retry_json = json.dumps(
            {
                "max_attempts": 3,
                "base_delay_seconds": 30,
                "max_delay_seconds": 900,
            }
        )
        connection.executemany(
            """
            INSERT INTO agents
                (id, name, role, model_role, instructions, default_retry, is_default,
                 runtime_type, provider_id, model_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    "agent-1",
                    "Referenced Agent",
                    "implementation",
                    "implementer",
                    "SECRET AGENT INSTRUCTIONS",
                    retry_json,
                    1,
                    "pi",
                    "provider",
                    "model",
                ),
                (
                    "unused-agent",
                    "Unused Agent",
                    "implementation",
                    "implementer",
                    "UNUSED SECRET INSTRUCTIONS",
                    retry_json,
                    0,
                    "pi",
                    "unused-provider",
                    "unused-model",
                ),
            ),
        )
        connection.execute(
            """
            INSERT INTO capabilities (id, name, description, tools)
            VALUES ('cap-1', 'Code', 'sensitive capability description', '[]')
            """
        )
        connection.execute(
            """
            INSERT INTO agent_capabilities (agent_id, capability_id)
            VALUES ('agent-1', 'cap-1')
            """
        )
        connection.executemany(
            """
            INSERT INTO plans
                (id, project_id, status, version, phase, iteration, data,
                 claimed_by, claimed_at, lease_expires_at, lease_seconds,
                 retry_not_before, paused, pause_requested, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, 0, 0, ?, ?)
            """,
            (
                ("p1", "project-1", "blocked", 7, "running", 1, json.dumps(p1), T0, T2),
                ("p2", "project-2", "idle", 1, "discovery", 1, json.dumps(p2), T0, T0),
            ),
        )
        connection.execute(
            """
            INSERT INTO planning_operations
                (id, plan_id, purpose, target_goal_id, status, created_at, updated_at,
                 started_at, completed_at, last_liveness_at, model_request_count,
                 tool_turn_count, runtime, provider_id, model_id, failure_kind,
                 retry_at, safe_message)
            VALUES
                ('planning-1', 'p1', 'goal_contract', 'g1', 'failed', ?, ?,
                 ?, ?, ?, 2, 1, 'openai', 'provider', 'model', 'rate_limit',
                 NULL, 'planner was rate limited')
            """,
            (T0, T1, T0, T1, T1),
        )
        connection.execute(
            """
            INSERT INTO execution_runs
                (id, plan_id, goal_id, task_id, status, started_at, completed_at)
            VALUES ('run-1', 'p1', 'g1', 't1', 'succeeded', ?, ?)
            """,
            (T0, T2),
        )
        connection.executemany(
            """
            INSERT INTO execution_attempts
                (id, run_id, plan_id, goal_id, task_id, number, task_attempt, status,
                 started_at, completed_at, last_liveness_at, timeout_seconds,
                 runtime, provider_id, model_id, failure_kind, provider_code,
                 retryable, retry_at, limit_scope, exit_code, safe_message,
                 stdout_tail, stderr_tail)
            VALUES (?, 'run-1', 'p1', 'g1', 't1', ?, ?, ?, ?, ?, ?, 300,
                    'pi', 'provider', 'model', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    "attempt-1",
                    1,
                    1,
                    "failed",
                    T0,
                    T1,
                    T1,
                    "rate_limit",
                    "429",
                    1,
                    T1,
                    "per_minute",
                    1,
                    "rate limited",
                    "safe stdout",
                    "safe stderr",
                ),
                (
                    "attempt-2",
                    2,
                    2,
                    "succeeded",
                    T1,
                    T2,
                    T2,
                    None,
                    None,
                    None,
                    None,
                    None,
                    0,
                    None,
                    "tests passed",
                    "",
                ),
            ),
        )
        agent_events = [
            (
                "usage-planner",
                "p1",
                None,
                None,
                None,
                None,
                0,
                0,
                "llm.call",
                "model.usage",
                "provider",
                "reported",
                1,
                None,
                {
                    "llm_calls": 2,
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "provider": "provider",
                    "model": "model",
                },
            ),
            (
                "usage-child-unavailable",
                "p1",
                "g1",
                "t1",
                "run-1",
                "attempt-2",
                2,
                1,
                "llm.call",
                "model.usage",
                "provider",
                "unavailable",
                1,
                1,
                {
                    "llm_calls": 1,
                    "prompt_tokens": None,
                    "completion_tokens": None,
                    "total_tokens": None,
                    "unavailable_reason": "provider_did_not_report_usage",
                    "provider": "provider",
                    "model": "model",
                },
            ),
            (
                "legacy-started",
                "p1",
                "g1",
                "t1",
                None,
                None,
                1,
                0,
                "agent.started",
                "agent.started",
                "legacy",
                "legacy_unknown",
                0,
                0,
                {"runtime": "pi"},
            ),
            (
                "orphan-event",
                "p1",
                "g-missing",
                "t-missing",
                None,
                None,
                1,
                0,
                "agent.failed",
                "agent.failed",
                "legacy",
                "legacy_unknown",
                0,
                0,
                {"kind": "tool_error"},
            ),
        ]
        connection.executemany(
            """
            INSERT INTO agent_events
                (event_id, plan_id, goal_id, task_id, run_id, attempt_id, attempt,
                 seq, type, observation_kind, source, quality, schema_version,
                 source_sequence, payload, occurred_at, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [row[:-1] + (json.dumps(row[-1]), T1, T1) for row in agent_events],
        )
        connection.execute(
            """
            INSERT INTO outbox
                (event_id, plan_id, type, payload, occurred_at, delivered_at)
            VALUES
                ('domain-1', 'p1', 'TaskRetried', ?, ?, ?)
            """,
            (json.dumps({"plan_id": "p1", "task_id": "t1"}), T1, T2),
        )
        connection.execute(
            """
            INSERT INTO plan_chat_messages (plan_id, role, content, meta, created_at)
            VALUES ('p1', 'user', 'retry the failed task', ?, ?)
            """,
            (json.dumps({"committed": True}), T1),
        )
        connection.execute(
            """
            INSERT INTO runtime_circuits
                (runtime, provider_id, model_id, failure_count, opened_at, retry_at,
                 last_failure_kind, safe_message, manual_intervention)
            VALUES ('pi', 'provider', 'model', 3, ?, ?, 'rate_limit', 'quota', 1)
            """,
            (T1, T2),
        )


def test_exporters_are_standalone_and_have_no_system_imports():
    for script in (SCRIPT, SNAPSHOT_SCRIPT):
        source = script.read_text(encoding="utf-8")
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
    exporter_source = SCRIPT.read_text(encoding="utf-8")
    assert "mode=ro" in exporter_source
    assert "PRAGMA query_only = ON" in exporter_source


def test_exports_every_plan_run_with_telemetry_metrics_insights_and_summary(tmp_path):
    database = tmp_path / "orchestrator.db"
    output = tmp_path / "all-plan-runs.json"
    _seed_database(database)
    before = _hash(database)

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--db",
            str(database),
            "--output",
            str(output),
            "--pretty",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert _hash(database) == before
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["schema_version"] == "1.1"
    assert report["source"]["read_only"] is True
    assert report["source"]["snapshot"] == "single SQLite read transaction"
    assert "secrets" in report["source"]["excluded_tables"]
    assert report["source"]["catalog_export"]["scope"] == "referenced_entries_only"
    assert report["catalog"]["providers"] == [{"id": "provider", "name": "Referenced Provider"}]
    assert report["catalog"]["models"] == [
        {
            "id": "model",
            "provider_id": "provider",
            "name": "Referenced Model",
        }
    ]
    assert report["catalog"]["agents"] == [
        {
            "id": "agent-1",
            "name": "Referenced Agent",
            "role": "implementation",
            "model_role": "implementer",
            "is_default": True,
            "runtime_type": "pi",
            "provider_id": "provider",
            "model_id": "model",
            "capability_ids": ["cap-1"],
        }
    ]
    serialized_report = json.dumps(report)
    assert "secret://referenced-provider-key" not in serialized_report
    assert "internal.example.test" not in serialized_report
    assert "SECRET AGENT INSTRUCTIONS" not in serialized_report
    assert "unused-provider" not in serialized_report
    assert "unused-model" not in serialized_report
    assert "unused-agent" not in serialized_report
    assert report["totals"] == {
        "plans": 2,
        "planning_operations": 1,
        "execution_runs": 1,
        "execution_attempts": 2,
        "telemetry_events": 4,
        "domain_events": 1,
    }

    plans = {item["plan"]["id"]: item for item in report["plans"]}
    assert set(plans) == {"p1", "p2"}
    exported = plans["p1"]
    assert exported["aggregate"]["brief"] == "ship a verified API"
    assert exported["planning_operations"][0]["id"] == "planning-1"
    run = exported["execution_runs"][0]
    assert run["id"] == "run-1"
    assert run["attempt_count"] == 2
    assert run["retry_count"] == 1
    assert [attempt["id"] for attempt in run["attempts"]] == [
        "attempt-1",
        "attempt-2",
    ]
    assert {event["event_id"] for event in run["telemetry"]} == {
        "usage-child-unavailable",
        "legacy-started",
    }
    assert {event["event_id"] for event in exported["unassigned_telemetry"]} == {
        "usage-planner",
        "orphan-event",
    }
    assert exported["metrics"]["llm"]["sessions"] == 2
    assert exported["metrics"]["llm"]["calls"] == 3
    assert exported["metrics"]["llm"]["total_tokens"] == 15
    assert exported["metrics"]["llm"]["coverage"] == {
        "observations": 2,
        "reported": 1,
        "estimated": 0,
        "unavailable": 1,
        "legacy_unknown": 0,
    }
    comparisons = report["performance_comparisons"]
    assert comparisons["measurement_notes"]["duration"].endswith("not provider HTTP latency.")
    attempt_comparison = comparisons["execution_attempts_by_runtime_provider_model"][0]
    assert attempt_comparison["identity"] == {
        "runtime": "pi",
        "provider_id": "provider",
        "model_id": "model",
    }
    assert attempt_comparison["attempts"] == 2
    assert attempt_comparison["success_rate"] == 0.5
    assert attempt_comparison["retry_attempts"] == 1
    assert attempt_comparison["duration"]["p50"] == 60.0
    planning_comparison = comparisons["planning_operations_by_runtime_provider_model"][0]
    assert planning_comparison["model_request_count"] == 2
    assert planning_comparison["duration"]["mean"] == 60.0
    usage_comparison = comparisons["model_usage_by_reported_provider_model"][0]
    assert usage_comparison["reported_identity"] == {
        "provider": "provider",
        "model": "model",
    }
    assert usage_comparison["usage"]["sessions"] == 2
    assert usage_comparison["usage"]["total_tokens"] == 15

    assert exported["metrics"]["agent"] == {
        "runs": 2,
        "finished": 2,
        "failed": 1,
        "failures_by_kind": {"rate_limit": 1},
        "source": "execution_ledger",
        "quality": "exact",
    }
    summary = exported["execution_summary"]
    assert summary["execution_runs"]["success_rate"] == 1.0
    assert summary["execution_runs"]["retry_count"] == 1
    assert summary["execution_attempts"]["by_status"] == {
        "failed": 1,
        "succeeded": 1,
    }
    assert summary["aggregate_status_counts"]["tasks"] == {"done": 1, "failed": 1}
    codes = {insight["code"] for insight in exported["insights"]}
    assert {
        "automatic_retries",
        "execution_failures",
        "planning_failures_or_backoff",
        "usage_unavailable",
        "uncorrelated_telemetry",
        "plan_blocked",
    } <= codes
    assert exported["referenced_runtime_circuits"][0]["manual_intervention"] is True
    assert exported["domain_events"][0]["payload"]["task_id"] == "t1"
    assert exported["chat"][0]["meta"] == {"committed": True}
    assert plans["p2"]["execution_runs"] == []
    assert plans["p2"]["insights"][0]["code"] == "no_execution_runs"


def test_plan_filter_writes_json_to_stdout_and_rejects_unknown_plan(tmp_path):
    database = tmp_path / "orchestrator.db"
    _seed_database(database)

    selected = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(database), "--plan-id", "p2"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert selected.returncode == 0, selected.stderr
    report = json.loads(selected.stdout)
    assert report["totals"]["plans"] == 1
    assert report["plans"][0]["plan"]["id"] == "p2"

    missing = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(database), "--plan-id", "missing"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert missing.returncode == 2
    assert "unknown plan id(s): missing" in missing.stderr
    assert missing.stdout == ""


def test_rejects_unmigrated_database_with_actionable_error(tmp_path):
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


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_writes_atomic_jsonl_bundle_with_manifest_hashes_and_no_catalog_secrets(
    tmp_path,
):
    database = tmp_path / "orchestrator.db"
    output_root = tmp_path / "exports" / "plan-runs"
    _seed_database(database)
    before = _hash(database)

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--db",
            str(database),
            "--format",
            "bundle",
            "--output-dir",
            str(output_root),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert _hash(database) == before
    bundle = Path(completed.stdout.strip())
    assert bundle.parent == output_root.resolve()
    expected_files = {
        "manifest.json",
        "catalog.json",
        "comparisons.json",
        "plans.jsonl",
        "planning_operations.jsonl",
        "runs.jsonl",
        "attempts.jsonl",
        "telemetry.jsonl",
        "execution_summaries.jsonl",
        "metrics.jsonl",
        "insights.jsonl",
        "domain_events.jsonl",
        "chat.jsonl",
        "runtime_circuits.jsonl",
    }
    assert {path.name for path in bundle.iterdir()} == expected_files
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["bundle_schema_version"] == "1.0"
    assert manifest["report_schema_version"] == "1.1"
    assert manifest["privacy"]["referenced_catalog_only"] is True
    assert manifest["files"]["plans.jsonl"]["records"] == 2
    assert manifest["files"]["attempts.jsonl"]["records"] == 2
    for filename, metadata in manifest["files"].items():
        assert _hash(bundle / filename) == metadata["sha256"]

    plans = _read_jsonl(bundle / "plans.jsonl")
    assert {record["plan_id"] for record in plans} == {"p1", "p2"}
    attempts = _read_jsonl(bundle / "attempts.jsonl")
    assert [record["id"] for record in attempts] == ["attempt-1", "attempt-2"]
    telemetry = _read_jsonl(bundle / "telemetry.jsonl")
    methods = {event["event_id"]: event["export_correlation"]["method"] for event in telemetry}
    assert methods["usage-child-unavailable"] == "run_id"
    assert methods["legacy-started"] == "unique_legacy_task_attempt"
    assert methods["orphan-event"] == "unassigned"

    bundle_text = "\n".join(
        path.read_text(encoding="utf-8") for path in bundle.iterdir() if path.is_file()
    )
    assert "secret://referenced-provider-key" not in bundle_text
    assert "internal.example.test" not in bundle_text
    assert "SECRET AGENT INSTRUCTIONS" not in bundle_text
    assert "unused-provider" not in bundle_text


def test_bundle_requires_explicit_output_directory(tmp_path):
    database = tmp_path / "orchestrator.db"
    _seed_database(database)

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(database), "--format", "bundle"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "--output-dir is required for --format bundle" in completed.stderr


def test_current_plan_snapshot_auto_selects_or_uses_project_and_remains_read_only(
    tmp_path,
):
    database = tmp_path / "orchestrator.db"
    _seed_database(database)
    before = _hash(database)

    automatic = subprocess.run(
        [sys.executable, str(SNAPSHOT_SCRIPT), "--db", str(database)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert automatic.returncode == 0, automatic.stderr
    assert _hash(database) == before
    snapshot = json.loads(automatic.stdout)
    assert snapshot["schema_version"] == "1.0"
    assert snapshot["snapshot_kind"] == "current_plan_debug"
    assert snapshot["selection"] == {
        "plan_id": "p1",
        "project_id": "project-1",
        "status": "blocked",
        "version": 7,
    }
    assert snapshot["source"]["selection"]["current"] is True
    assert snapshot["plan"]["plan"]["id"] == "p1"
    assert snapshot["plan"]["execution_runs"][0]["id"] == "run-1"
    assert snapshot["catalog"]["agents"][0]["id"] == "agent-1"

    by_project = subprocess.run(
        [
            sys.executable,
            str(SNAPSHOT_SCRIPT),
            "--db",
            str(database),
            "--project-id",
            "project-2",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert by_project.returncode == 0, by_project.stderr
    project_snapshot = json.loads(by_project.stdout)
    assert project_snapshot["selection"]["plan_id"] == "p2"
    assert project_snapshot["plan"]["execution_runs"] == []
    assert project_snapshot["catalog"]["agents"] == []


def test_current_plan_snapshot_refuses_ambiguous_selection(tmp_path):
    database = tmp_path / "orchestrator.db"
    _seed_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE plans SET status = 'waiting' WHERE id = 'p2'")

    completed = subprocess.run(
        [sys.executable, str(SNAPSHOT_SCRIPT), "--db", str(database)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "current plan is ambiguous" in completed.stderr
    assert "p1 (blocked)" in completed.stderr
    assert "p2 (waiting)" in completed.stderr
    assert completed.stdout == ""
