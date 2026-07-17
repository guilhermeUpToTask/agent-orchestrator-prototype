#!/usr/bin/env python3
"""Export every persisted ProjectPlan run from SQLite without joining the system.

The exporter is intentionally standalone and stdlib-only. It never constructs the
application container, imports domain/runtime code, calls an API, or writes to the
orchestrator database. SQLite is opened with ``mode=ro`` and ``query_only=ON`` and
all reads happen in one snapshot transaction.

The JSON report contains the plan aggregate, planning operations, execution runs
and attempts, correlated telemetry, domain events, chat context, truthful metrics,
derived execution summaries, and explicitly labelled insights. Secret, config,
provider, model, and agent catalog tables are deliberately excluded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

SCHEMA_VERSION = "1.1"
SNAPSHOT_SCHEMA_VERSION = "1.0"
DB_FILENAME = "orchestrator.db"
REQUIRED_TABLES = frozenset(
    {
        "plans",
        "planning_operations",
        "execution_runs",
        "execution_attempts",
        "agent_events",
        "outbox",
        "plan_chat_messages",
        "runtime_circuits",
        "providers",
        "models",
        "agents",
        "agent_capabilities",
    }
)
EXCLUDED_TABLES = [
    "secrets",
    "config",
    "capabilities",
]
CATALOG_REDACTED_FIELDS = {
    "providers": ["api_key_ref", "base_url"],
    "agents": ["instructions", "default_retry"],
}


class ExportError(RuntimeError):
    """The requested database cannot produce a trustworthy export."""


def _rows(
    connection: sqlite3.Connection,
    sql: str,
    params: Sequence[object] = (),
) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(sql, params).fetchall()]


def _json_value(raw: object, *, field: str, issues: list[dict[str, str]]) -> Any:
    if raw is None:
        return None
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        issues.append(
            {
                "code": "invalid_persisted_json",
                "field": field,
                "message": f"Persisted {field} could not be decoded as JSON.",
            }
        )
        return {"_raw": raw, "_decode_error": "invalid_json"}


def _parse_time(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _duration_seconds(start: object, end: object) -> float | None:
    started = _parse_time(start)
    completed = _parse_time(end)
    if started is None or completed is None:
        return None
    return max(0.0, (completed - started).total_seconds())


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 6)
    rank = (len(ordered) - 1) * fraction
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 6)


def _duration_distribution(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    materialized = list(rows)
    values = [
        duration
        for row in materialized
        if (duration := _duration_seconds(row.get("started_at"), row.get("completed_at")))
        is not None
    ]
    return {
        "measurement": "end_to_end_operation_wall_clock",
        "unit": "seconds",
        "observations": len(values),
        "unavailable": len(materialized) - len(values),
        "min": round(min(values), 6) if values else None,
        "mean": round(sum(values) / len(values), 6) if values else None,
        "p50": _percentile(values, 0.50),
        "p95": _percentile(values, 0.95),
        "max": round(max(values), 6) if values else None,
    }


def _identity_key(row: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    return (
        None if row.get("runtime") is None else str(row["runtime"]),
        None if row.get("provider_id") is None else str(row["provider_id"]),
        None if row.get("model_id") is None else str(row["model_id"]),
    )


def _identity(key: tuple[str | None, str | None, str | None]) -> dict[str, str | None]:
    return {
        "runtime": key[0],
        "provider_id": key[1],
        "model_id": key[2],
    }


def _performance_comparisons(plans: list[dict[str, Any]]) -> dict[str, Any]:
    attempts = [
        attempt for plan in plans for run in plan["execution_runs"] for attempt in run["attempts"]
    ]
    planning = [operation for plan in plans for operation in plan["planning_operations"]]
    events = [
        event for plan in plans for run in plan["execution_runs"] for event in run["telemetry"]
    ] + [event for plan in plans for event in plan["unassigned_telemetry"]]

    attempt_groups: dict[tuple[str | None, str | None, str | None], list[dict[str, Any]]] = (
        defaultdict(list)
    )
    for attempt in attempts:
        attempt_groups[_identity_key(attempt)].append(attempt)
    attempt_comparisons: list[dict[str, Any]] = []
    for key, rows in sorted(
        attempt_groups.items(),
        key=lambda item: tuple(value or "" for value in item[0]),
    ):
        statuses = _status_counts(rows)
        terminal = sum(statuses.get(name, 0) for name in ("succeeded", "failed", "abandoned"))
        failures = Counter(
            str(row.get("failure_kind") or "unknown")
            for row in rows
            if row.get("status") == "failed"
        )
        attempt_comparisons.append(
            {
                "identity": _identity(key),
                "attempts": len(rows),
                "by_status": statuses,
                "success_rate": (
                    round(statuses.get("succeeded", 0) / terminal, 6) if terminal else None
                ),
                "retry_attempts": sum(int(row.get("number") or 0) > 1 for row in rows),
                "failures_by_kind": dict(sorted(failures.items())),
                "duration": _duration_distribution(rows),
            }
        )

    planning_groups: dict[tuple[str | None, str | None, str | None], list[dict[str, Any]]] = (
        defaultdict(list)
    )
    for operation in planning:
        planning_groups[_identity_key(operation)].append(operation)
    planning_comparisons: list[dict[str, Any]] = []
    for key, rows in sorted(
        planning_groups.items(),
        key=lambda item: tuple(value or "" for value in item[0]),
    ):
        planning_comparisons.append(
            {
                "identity": _identity(key),
                "operations": len(rows),
                "by_status": _status_counts(rows),
                "model_request_count": sum(
                    int(row.get("model_request_count") or 0) for row in rows
                ),
                "tool_turn_count": sum(int(row.get("tool_turn_count") or 0) for row in rows),
                "duration": _duration_distribution(rows),
            }
        )

    usage_groups: dict[tuple[str | None, str | None], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if event.get("observation_kind") != "model.usage":
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        key = (
            None if payload.get("provider") is None else str(payload["provider"]),
            None if payload.get("model") is None else str(payload["model"]),
        )
        usage_groups[key].append(event)
    usage_comparisons: list[dict[str, Any]] = []
    for key, rows in sorted(
        usage_groups.items(),
        key=lambda item: tuple(value or "" for value in item[0]),
    ):
        combined = dict(_metrics(rows, [])["llm"])
        combined.pop("scopes", None)
        usage_comparisons.append(
            {
                "reported_identity": {
                    "provider": key[0],
                    "model": key[1],
                },
                "usage": combined,
            }
        )

    return {
        "measurement_notes": {
            "duration": (
                "Durations are orchestrator operation wall-clock measurements, "
                "not provider HTTP latency."
            ),
            "usage_identity": (
                "Usage groups use provider/model strings reported in telemetry; "
                "they are not guessed from mutable catalog bindings."
            ),
            "agent_history": (
                "Execution attempts do not persist agent_id, so historical "
                "agent-performance attribution is not claimed."
            ),
        },
        "execution_attempts_by_runtime_provider_model": attempt_comparisons,
        "planning_operations_by_runtime_provider_model": planning_comparisons,
        "model_usage_by_reported_provider_model": usage_comparisons,
    }


def _collect_agent_ids(value: Any, found: set[str]) -> None:
    if isinstance(value, dict):
        agent_id = value.get("agent_id")
        if isinstance(agent_id, str) and agent_id:
            found.add(agent_id)
        role_agents = value.get("role_agent_ids")
        if isinstance(role_agents, dict):
            found.update(
                agent for agent in role_agents.values() if isinstance(agent, str) and agent
            )
        for nested in value.values():
            _collect_agent_ids(nested, found)
    elif isinstance(value, list):
        for nested in value:
            _collect_agent_ids(nested, found)


def _catalog_rows_by_ids(
    connection: sqlite3.Connection,
    table: str,
    identifiers: set[str],
) -> list[dict[str, Any]]:
    if not identifiers:
        return []
    placeholders = ", ".join("?" for _ in identifiers)
    return _rows(
        connection,
        f'SELECT * FROM "{table}" WHERE id IN ({placeholders}) ORDER BY id',
        tuple(sorted(identifiers)),
    )


def _referenced_catalog(
    connection: sqlite3.Connection,
    plans: list[dict[str, Any]],
) -> dict[str, Any]:
    agent_ids: set[str] = set()
    provider_ids: set[str] = set()
    model_ids: set[str] = set()
    for plan in plans:
        _collect_agent_ids(plan["aggregate"], agent_ids)
        operational_rows = list(plan["planning_operations"])
        operational_rows.extend(
            attempt for run in plan["execution_runs"] for attempt in run["attempts"]
        )
        operational_rows.extend(plan["referenced_runtime_circuits"])
        for row in operational_rows:
            if row.get("provider_id") is not None:
                provider_ids.add(str(row["provider_id"]))
            if row.get("model_id") is not None:
                model_ids.add(str(row["model_id"]))

    agent_rows = _catalog_rows_by_ids(connection, "agents", agent_ids)
    for row in agent_rows:
        if row.get("provider_id") is not None:
            provider_ids.add(str(row["provider_id"]))
        if row.get("model_id") is not None:
            model_ids.add(str(row["model_id"]))

    model_rows = _catalog_rows_by_ids(connection, "models", model_ids)
    provider_ids.update(str(row["provider_id"]) for row in model_rows)
    provider_rows = _catalog_rows_by_ids(connection, "providers", provider_ids)

    capabilities_by_agent: dict[str, list[str]] = defaultdict(list)
    if agent_ids:
        placeholders = ", ".join("?" for _ in agent_ids)
        links = _rows(
            connection,
            (
                "SELECT agent_id, capability_id FROM agent_capabilities "
                f"WHERE agent_id IN ({placeholders}) "
                "ORDER BY agent_id, capability_id"
            ),
            tuple(sorted(agent_ids)),
        )
        for link in links:
            capabilities_by_agent[str(link["agent_id"])].append(str(link["capability_id"]))

    providers = [
        {
            "id": row["id"],
            "name": row["name"],
        }
        for row in provider_rows
    ]
    models = [
        {
            "id": row["id"],
            "provider_id": row["provider_id"],
            "name": row["name"],
        }
        for row in model_rows
    ]
    agents = [
        {
            "id": row["id"],
            "name": row["name"],
            "role": row["role"],
            "model_role": row["model_role"],
            "is_default": bool(row.get("is_default")),
            "runtime_type": row.get("runtime_type"),
            "provider_id": row.get("provider_id"),
            "model_id": row.get("model_id"),
            "capability_ids": capabilities_by_agent.get(str(row["id"]), []),
        }
        for row in agent_rows
    ]
    return {
        "policy": {
            "scope": "referenced_entries_only",
            "allowlisted_fields_only": True,
            "redacted_fields": CATALOG_REDACTED_FIELDS,
            "catalog_is_current_not_historical": True,
        },
        "providers": providers,
        "models": models,
        "agents": agents,
        "unresolved_references": {
            "provider_ids": sorted(provider_ids - {str(row["id"]) for row in provider_rows}),
            "model_ids": sorted(model_ids - {str(row["id"]) for row in model_rows}),
            "agent_ids": sorted(agent_ids - {str(row["id"]) for row in agent_rows}),
        },
    }


def _status_counts(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    return dict(sorted(Counter(str(row.get("status") or "unknown") for row in rows).items()))


def _decode_rows(
    rows: list[dict[str, Any]],
    fields: Sequence[str],
    issues: list[dict[str, str]],
    *,
    prefix: str,
) -> list[dict[str, Any]]:
    for row in rows:
        identity = row.get("event_id") or row.get("id") or "unknown"
        for field in fields:
            row[field] = _json_value(
                row.get(field),
                field=f"{prefix}[{identity}].{field}",
                issues=issues,
            )
    return rows


def _usage_scope() -> dict[str, Any]:
    return {
        "sessions": 0,
        "calls": 0,
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
        "coverage": {
            "observations": 0,
            "reported": 0,
            "estimated": 0,
            "unavailable": 0,
            "legacy_unknown": 0,
        },
    }


def _metrics(events: list[dict[str, Any]], attempts: list[dict[str, Any]]) -> dict[str, Any]:
    """Mirror the live metrics read model without importing application code."""
    scopes = {
        "planner": _usage_scope(),
        "child": _usage_scope(),
        "combined": _usage_scope(),
    }
    for event in events:
        if event.get("observation_kind") != "model.usage":
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        scope_name = "child" if event.get("task_id") is not None else "planner"
        for name in (scope_name, "combined"):
            scope = scopes[name]
            scope["sessions"] += 1
            scope["calls"] += int(payload.get("llm_calls") or 0)
            coverage = scope["coverage"]
            coverage["observations"] += 1
            quality = str(event.get("quality") or "legacy_unknown")
            coverage[quality] = coverage.get(quality, 0) + 1
            for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
                raw = payload.get(field)
                if raw is not None:
                    scope[field] = (scope[field] or 0) + int(raw)

    failures: Counter[str] = Counter()
    finished = 0
    failed = 0
    for attempt in attempts:
        status = str(attempt.get("status") or "unknown")
        if status in {"succeeded", "failed", "abandoned"}:
            finished += 1
        if status == "failed":
            failed += 1
            failures[str(attempt.get("failure_kind") or "unknown")] += 1
    return {
        "llm": {**scopes["combined"], "scopes": scopes},
        "agent": {
            "runs": len(attempts),
            "finished": finished,
            "failed": failed,
            "failures_by_kind": dict(sorted(failures.items())),
            "source": "execution_ledger",
            "quality": "exact",
        },
    }


def _aggregate_counts(aggregate: Any) -> dict[str, Any]:
    if not isinstance(aggregate, dict):
        return {
            "cycles": {},
            "goals": {},
            "tasks": {},
        }
    cycles = aggregate.get("cycles")
    cycle_rows = cycles if isinstance(cycles, list) else []
    if cycle_rows:
        goals = [
            goal
            for cycle in cycle_rows
            if isinstance(cycle, dict)
            for goal in (cycle.get("goals") or [])
            if isinstance(goal, dict)
        ]
    else:
        raw_goals = aggregate.get("goals")
        goals = [goal for goal in (raw_goals or []) if isinstance(goal, dict)]
    tasks = [task for goal in goals for task in (goal.get("tasks") or []) if isinstance(task, dict)]
    return {
        "cycles": _status_counts(cycle_rows),
        "goals": _status_counts(goals),
        "tasks": _status_counts(tasks),
    }


def _correlate_telemetry(
    events: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    run_ids = {str(run["id"]) for run in runs}
    attempt_to_run = {str(attempt["id"]): str(attempt["run_id"]) for attempt in attempts}
    fallback: dict[tuple[str | None, str, int], set[str]] = defaultdict(set)
    for attempt in attempts:
        fallback[
            (
                None if attempt.get("goal_id") is None else str(attempt["goal_id"]),
                str(attempt["task_id"]),
                int(attempt["number"]),
            )
        ].add(str(attempt["run_id"]))

    correlated: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unassigned: list[dict[str, Any]] = []
    for event in events:
        run_id: str | None = None
        method = "unassigned"
        explicit_run = event.get("run_id")
        explicit_attempt = event.get("attempt_id")
        if explicit_run is not None and str(explicit_run) in run_ids:
            run_id = str(explicit_run)
            method = "run_id"
        elif explicit_attempt is not None:
            run_id = attempt_to_run.get(str(explicit_attempt))
            if run_id is not None:
                method = "attempt_id"
        elif event.get("task_id") is not None and int(event.get("attempt") or 0) > 0:
            key = (
                None if event.get("goal_id") is None else str(event["goal_id"]),
                str(event["task_id"]),
                int(event["attempt"]),
            )
            candidates = fallback.get(key, set())
            if len(candidates) == 1:
                run_id = next(iter(candidates))
                method = "unique_legacy_task_attempt"
            elif len(candidates) > 1:
                method = "ambiguous_legacy_task_attempt"
        exported_event = {
            **event,
            "export_correlation": {
                "run_id": run_id,
                "method": method,
            },
        }
        if run_id is None:
            unassigned.append(exported_event)
        else:
            correlated[run_id].append(exported_event)
    return dict(correlated), unassigned


def _execution_window(runs: list[dict[str, Any]]) -> dict[str, Any]:
    starts = sorted(value for row in runs if isinstance((value := row.get("started_at")), str))
    ends = sorted(value for row in runs if isinstance((value := row.get("completed_at")), str))
    started_at = starts[0] if starts else None
    completed_at = ends[-1] if ends else None
    return {
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_seconds": _duration_seconds(started_at, completed_at),
    }


def _execution_summary(
    plan: dict[str, Any],
    aggregate: Any,
    planning: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    events: list[dict[str, Any]],
    domain_events: list[dict[str, Any]],
) -> dict[str, Any]:
    run_counts = Counter(str(run.get("status") or "unknown") for run in runs)
    terminal_runs = sum(run_counts[name] for name in ("succeeded", "failed", "abandoned"))
    retry_count = sum(max(0, int(run.get("attempt_count") or 0) - 1) for run in runs)
    return {
        "plan_status": plan.get("status"),
        "legacy_phase": plan.get("phase"),
        "version": plan.get("version"),
        "iteration": plan.get("iteration"),
        "aggregate_status_counts": _aggregate_counts(aggregate),
        "planning_operations": {
            "total": len(planning),
            "by_status": _status_counts(planning),
        },
        "execution_runs": {
            "total": len(runs),
            "by_status": dict(sorted(run_counts.items())),
            "terminal": terminal_runs,
            "success_rate": (
                round(run_counts["succeeded"] / terminal_runs, 4) if terminal_runs else None
            ),
            "retry_count": retry_count,
            "tasks_touched": len(
                {(str(run.get("goal_id")), str(run.get("task_id"))) for run in runs}
            ),
            "window": _execution_window(runs),
        },
        "execution_attempts": {
            "total": len(attempts),
            "by_status": _status_counts(attempts),
            "by_failure_kind": dict(
                sorted(
                    Counter(
                        str(attempt.get("failure_kind") or "unknown")
                        for attempt in attempts
                        if attempt.get("status") == "failed"
                    ).items()
                )
            ),
        },
        "telemetry": {
            "total": len(events),
            "by_type": dict(
                sorted(Counter(str(event.get("type") or "unknown") for event in events).items())
            ),
            "by_quality": dict(
                sorted(
                    Counter(
                        str(event.get("quality") or "legacy_unknown") for event in events
                    ).items()
                )
            ),
        },
        "domain_events": {
            "total": len(domain_events),
            "by_type": dict(
                sorted(
                    Counter(str(event.get("type") or "unknown") for event in domain_events).items()
                )
            ),
        },
    }


def _insights(
    plan: dict[str, Any],
    aggregate: Any,
    planning: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    metrics: dict[str, Any],
    unassigned_events: list[dict[str, Any]],
    decode_issues: list[dict[str, str]],
) -> list[dict[str, Any]]:
    insights: list[dict[str, Any]] = []

    def add(severity: str, code: str, message: str, evidence: dict[str, Any]) -> None:
        insights.append(
            {
                "severity": severity,
                "code": code,
                "message": message,
                "evidence": evidence,
            }
        )

    if not runs:
        add(
            "info",
            "no_execution_runs",
            "The plan has no persisted task execution runs.",
            {"run_count": 0},
        )
    open_runs = [run["id"] for run in runs if run.get("status") in {"running", "retrying"}]
    open_attempts = [attempt["id"] for attempt in attempts if attempt.get("status") == "running"]
    if open_runs or open_attempts:
        add(
            "warning",
            "open_execution",
            "Execution ledger rows are still active and may represent live or stale work.",
            {"run_ids": open_runs, "attempt_ids": open_attempts},
        )
    failed_attempts = [attempt for attempt in attempts if attempt.get("status") == "failed"]
    if failed_attempts:
        add(
            "warning",
            "execution_failures",
            "One or more concrete execution attempts failed.",
            {
                "count": len(failed_attempts),
                "failure_kinds": dict(
                    sorted(
                        Counter(
                            str(attempt.get("failure_kind") or "unknown")
                            for attempt in failed_attempts
                        ).items()
                    )
                ),
            },
        )
    retries = sum(max(0, int(run.get("attempt_count") or 0) - 1) for run in runs)
    if retries:
        add(
            "info",
            "automatic_retries",
            "Some logical runs required more than one concrete attempt.",
            {"retry_count": retries},
        )
    troubled_planning = [
        operation for operation in planning if operation.get("status") in {"failed", "backing_off"}
    ]
    if troubled_planning:
        add(
            "warning",
            "planning_failures_or_backoff",
            "Planning operations contain failures or durable backoff.",
            {
                "operation_ids": [operation["id"] for operation in troubled_planning],
                "by_status": _status_counts(troubled_planning),
            },
        )
    coverage = metrics["llm"]["coverage"]
    if coverage.get("unavailable", 0):
        add(
            "warning",
            "usage_unavailable",
            "Provider token usage was unavailable for some model sessions.",
            {"count": coverage["unavailable"]},
        )
    if coverage.get("legacy_unknown", 0):
        add(
            "info",
            "legacy_usage_quality",
            "Some model usage has legacy-unknown provenance or quality.",
            {"count": coverage["legacy_unknown"]},
        )
    uncorrelated = [
        event
        for event in unassigned_events
        if event.get("task_id") is not None
        or event.get("run_id") is not None
        or event.get("attempt_id") is not None
    ]
    if uncorrelated:
        add(
            "warning",
            "uncorrelated_telemetry",
            "Task/run telemetry could not be linked to a persisted execution run.",
            {"event_ids": [event["event_id"] for event in uncorrelated]},
        )
    if attempts and metrics["llm"]["scopes"]["child"]["coverage"]["observations"] == 0:
        add(
            "info",
            "child_usage_coverage_gap",
            "Execution attempts exist but no child-agent model usage observations were persisted.",
            {"attempt_count": len(attempts)},
        )
    block = aggregate.get("block") if isinstance(aggregate, dict) else None
    if plan.get("status") == "blocked" or (
        isinstance(block, dict) and block.get("resolved_at") is None
    ):
        add(
            "warning",
            "plan_blocked",
            "The plan snapshot contains an unresolved structured block.",
            {
                "kind": block.get("kind") if isinstance(block, dict) else None,
                "stage": block.get("stage") if isinstance(block, dict) else None,
            },
        )
    for issue in decode_issues:
        add(
            "warning",
            issue["code"],
            issue["message"],
            {"field": issue["field"]},
        )
    return insights


def _referenced_circuits(
    aggregate: Any,
    circuits: dict[tuple[str, str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(aggregate, dict):
        return []
    block = aggregate.get("block")
    refs = block.get("evidence_refs", []) if isinstance(block, dict) else []
    found: list[dict[str, Any]] = []
    for ref in refs:
        if not isinstance(ref, str) or not ref.startswith("runtime-circuit://"):
            continue
        parts = ref.removeprefix("runtime-circuit://").split("/", 2)
        if len(parts) != 3:
            continue
        circuit = circuits.get((parts[0], parts[1], parts[2]))
        if circuit is not None:
            found.append(circuit)
    return found


def _export_plan(
    connection: sqlite3.Connection,
    plan: dict[str, Any],
    circuits: dict[tuple[str, str, str], dict[str, Any]],
) -> dict[str, Any]:
    plan_id = str(plan["id"])
    issues: list[dict[str, str]] = []
    aggregate = _json_value(
        plan.pop("data"),
        field=f"plans[{plan_id}].data",
        issues=issues,
    )
    plan["paused"] = bool(plan.get("paused"))
    plan["pause_requested"] = bool(plan.get("pause_requested"))

    planning = _rows(
        connection,
        "SELECT * FROM planning_operations WHERE plan_id = ? ORDER BY created_at, id",
        (plan_id,),
    )
    runs = _rows(
        connection,
        "SELECT * FROM execution_runs WHERE plan_id = ? ORDER BY started_at, id",
        (plan_id,),
    )
    attempts = _rows(
        connection,
        "SELECT * FROM execution_attempts WHERE plan_id = ? ORDER BY started_at, id",
        (plan_id,),
    )
    for attempt in attempts:
        if attempt.get("retryable") is not None:
            attempt["retryable"] = bool(attempt["retryable"])
    events = _decode_rows(
        _rows(
            connection,
            "SELECT * FROM agent_events WHERE plan_id = ? ORDER BY id",
            (plan_id,),
        ),
        ("payload",),
        issues,
        prefix="agent_events",
    )
    domain_events = _decode_rows(
        _rows(
            connection,
            "SELECT * FROM outbox WHERE plan_id = ? ORDER BY id",
            (plan_id,),
        ),
        ("payload",),
        issues,
        prefix="outbox",
    )
    chat = _decode_rows(
        _rows(
            connection,
            "SELECT * FROM plan_chat_messages WHERE plan_id = ? ORDER BY id",
            (plan_id,),
        ),
        ("meta",),
        issues,
        prefix="plan_chat_messages",
    )

    telemetry_by_run, unassigned_telemetry = _correlate_telemetry(events, runs, attempts)
    attempts_by_run: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for attempt in attempts:
        attempts_by_run[str(attempt["run_id"])].append(attempt)
    enriched_runs: list[dict[str, Any]] = []
    for run in runs:
        run_id = str(run["id"])
        run_attempts = attempts_by_run.get(run_id, [])
        run_telemetry = telemetry_by_run.get(run_id, [])
        enriched = {
            **run,
            "attempts": run_attempts,
            "telemetry": run_telemetry,
            "attempt_count": len(run_attempts),
            "retry_count": max(0, len(run_attempts) - 1),
            "telemetry_event_count": len(run_telemetry),
            "duration_seconds": _duration_seconds(run.get("started_at"), run.get("completed_at")),
        }
        enriched_runs.append(enriched)

    metrics = _metrics(events, attempts)
    summary = _execution_summary(
        plan,
        aggregate,
        planning,
        enriched_runs,
        attempts,
        events,
        domain_events,
    )
    return {
        "plan": plan,
        "aggregate": aggregate,
        "execution_summary": summary,
        "metrics": metrics,
        "insights": _insights(
            plan,
            aggregate,
            planning,
            enriched_runs,
            attempts,
            metrics,
            unassigned_telemetry,
            issues,
        ),
        "planning_operations": planning,
        "execution_runs": enriched_runs,
        "unassigned_telemetry": unassigned_telemetry,
        "domain_events": domain_events,
        "chat": chat,
        "referenced_runtime_circuits": _referenced_circuits(aggregate, circuits),
    }


def _schema_revision(connection: sqlite3.Connection, tables: set[str]) -> str | None:
    if "alembic_version" not in tables:
        return None
    row = connection.execute("SELECT version_num FROM alembic_version LIMIT 1").fetchone()
    return None if row is None else str(row[0])


def _select_plan_rows(
    plan_rows: list[dict[str, Any]],
    *,
    plan_ids: set[str] | None,
    project_id: str | None,
    current: bool,
) -> list[dict[str, Any]]:
    selector_count = int(plan_ids is not None) + int(project_id is not None) + int(current)
    if selector_count > 1:
        raise ExportError("plan_ids, project_id, and current selection are mutually exclusive")
    available_ids = {str(plan["id"]) for plan in plan_rows}
    if plan_ids is not None:
        missing_ids = sorted(plan_ids - available_ids)
        if missing_ids:
            raise ExportError("unknown plan id(s): " + ", ".join(missing_ids))
        return [plan for plan in plan_rows if str(plan["id"]) in plan_ids]
    if project_id is not None:
        matches = [plan for plan in plan_rows if plan.get("project_id") == project_id]
        if not matches:
            raise ExportError(f"no plan found for project id: {project_id}")
        return matches
    if not current:
        return plan_rows
    if not plan_rows:
        raise ExportError("cannot snapshot current plan: database contains no plans")
    if len(plan_rows) == 1:
        return plan_rows
    non_idle = [plan for plan in plan_rows if plan.get("status") != "idle"]
    if len(non_idle) == 1:
        return non_idle
    candidates = non_idle or plan_rows
    details = ", ".join(f"{plan['id']} ({plan.get('status') or 'unknown'})" for plan in candidates)
    raise ExportError(
        f"current plan is ambiguous; use --plan-id or --project-id. Candidates: {details}"
    )


def export_database(
    db_path: Path,
    plan_ids: set[str] | None = None,
    *,
    project_id: str | None = None,
    current: bool = False,
) -> dict[str, Any]:
    """Return one consistent read-only export of the requested SQLite database."""
    resolved = db_path.expanduser().resolve()
    if not resolved.is_file():
        raise ExportError(f"database does not exist: {resolved}")
    uri = f"{resolved.as_uri()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    except sqlite3.Error as exc:
        raise ExportError(f"could not open database read-only: {exc}") from exc
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        if int(connection.execute("PRAGMA query_only").fetchone()[0]) != 1:
            raise ExportError("SQLite did not enable query_only mode")
        connection.execute("BEGIN")
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        missing = sorted(REQUIRED_TABLES - tables)
        if missing:
            raise ExportError(
                "database schema is missing required tables; run migrations first: "
                + ", ".join(missing)
            )
        table_counts = {
            table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in sorted(REQUIRED_TABLES)
        }
        circuit_rows = _rows(
            connection,
            "SELECT * FROM runtime_circuits ORDER BY runtime, provider_id, model_id",
        )
        for circuit in circuit_rows:
            circuit["manual_intervention"] = bool(circuit.get("manual_intervention"))
        circuits = {
            (
                str(circuit["runtime"]),
                str(circuit["provider_id"]),
                str(circuit["model_id"]),
            ): circuit
            for circuit in circuit_rows
        }
        plan_rows = _select_plan_rows(
            _rows(connection, "SELECT * FROM plans ORDER BY created_at, id"),
            plan_ids=plan_ids,
            project_id=project_id,
            current=current,
        )
        plans = [_export_plan(connection, plan, circuits) for plan in plan_rows]
        catalog = _referenced_catalog(connection, plans)
        comparisons = _performance_comparisons(plans)
        result = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": {
                "database": str(resolved),
                "database_schema_revision": _schema_revision(connection, tables),
                "read_only": True,
                "snapshot": "single SQLite read transaction",
                "included_tables": sorted(REQUIRED_TABLES),
                "excluded_tables": EXCLUDED_TABLES,
                "catalog_export": {
                    "scope": "referenced_entries_only",
                    "redacted_fields": CATALOG_REDACTED_FIELDS,
                },
                "selection": {
                    "plan_ids": sorted(plan_ids) if plan_ids is not None else None,
                    "project_id": project_id,
                    "current": current,
                },
                "table_row_counts": table_counts,
            },
            "totals": {
                "plans": len(plans),
                "planning_operations": sum(len(plan["planning_operations"]) for plan in plans),
                "execution_runs": sum(len(plan["execution_runs"]) for plan in plans),
                "execution_attempts": sum(
                    sum(len(run["attempts"]) for run in plan["execution_runs"]) for plan in plans
                ),
                "telemetry_events": sum(
                    plan["execution_summary"]["telemetry"]["total"] for plan in plans
                ),
                "domain_events": sum(len(plan["domain_events"]) for plan in plans),
            },
            "catalog": catalog,
            "performance_comparisons": comparisons,
            "plans": plans,
        }
        connection.rollback()
        return result
    except sqlite3.Error as exc:
        raise ExportError(f"database export failed: {exc}") from exc
    finally:
        connection.close()


def write_json_report(report: dict[str, Any], output: str, *, pretty: bool) -> None:
    rendered = (
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2 if pretty else None,
            sort_keys=pretty,
        )
        + "\n"
    )
    if output == "-":
        sys.stdout.write(rendered)
        return
    destination = Path(output).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _bundle_records(report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    records: dict[str, list[dict[str, Any]]] = {
        "plans.jsonl": [],
        "planning_operations.jsonl": [],
        "runs.jsonl": [],
        "attempts.jsonl": [],
        "telemetry.jsonl": [],
        "execution_summaries.jsonl": [],
        "metrics.jsonl": [],
        "insights.jsonl": [],
        "domain_events.jsonl": [],
        "chat.jsonl": [],
        "runtime_circuits.jsonl": [],
    }
    for exported in report["plans"]:
        plan_id = str(exported["plan"]["id"])
        records["plans.jsonl"].append(
            {
                "plan_id": plan_id,
                "plan": exported["plan"],
                "aggregate": exported["aggregate"],
            }
        )
        records["planning_operations.jsonl"].extend(exported["planning_operations"])
        records["execution_summaries.jsonl"].append(
            {
                "plan_id": plan_id,
                "execution_summary": exported["execution_summary"],
            }
        )
        records["metrics.jsonl"].append(
            {
                "plan_id": plan_id,
                "metrics": exported["metrics"],
            }
        )
        records["insights.jsonl"].extend(
            {"plan_id": plan_id, **insight} for insight in exported["insights"]
        )
        records["domain_events.jsonl"].extend(exported["domain_events"])
        records["chat.jsonl"].extend(exported["chat"])
        records["runtime_circuits.jsonl"].extend(
            {"export_plan_id": plan_id, **circuit}
            for circuit in exported["referenced_runtime_circuits"]
        )
        for run in exported["execution_runs"]:
            records["runs.jsonl"].append(
                {key: value for key, value in run.items() if key not in {"attempts", "telemetry"}}
            )
            records["attempts.jsonl"].extend(run["attempts"])
            records["telemetry.jsonl"].extend(run["telemetry"])
        records["telemetry.jsonl"].extend(exported["unassigned_telemetry"])
    return records


def _jsonl_bytes(records: list[dict[str, Any]]) -> bytes:
    if not records:
        return b""
    return (
        "\n".join(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")) for record in records
        )
        + "\n"
    ).encode()


def _write_bundle_file(path: Path, content: bytes) -> None:
    with path.open("wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def write_bundle(report: dict[str, Any], output_root: Path) -> Path:
    """Atomically write a versioned JSON/JSONL analytics bundle."""
    root = output_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    generated = datetime.fromisoformat(str(report["generated_at"]).replace("Z", "+00:00"))
    timestamp = generated.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    destination = root / f"plan-runs-{timestamp}"
    if destination.exists():
        raise ExportError(f"bundle destination already exists: {destination}")
    temporary = Path(tempfile.mkdtemp(prefix=".plan-runs-", dir=root))
    try:
        files: dict[str, dict[str, Any]] = {}
        for filename, records in _bundle_records(report).items():
            content = _jsonl_bytes(records)
            _write_bundle_file(temporary / filename, content)
            files[filename] = {
                "format": "jsonl",
                "records": len(records),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        json_documents = {
            "catalog.json": report["catalog"],
            "comparisons.json": report["performance_comparisons"],
        }
        for filename, document in json_documents.items():
            content = (
                json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            ).encode()
            _write_bundle_file(temporary / filename, content)
            files[filename] = {
                "format": "json",
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        manifest = {
            "bundle_schema_version": "1.0",
            "report_schema_version": report["schema_version"],
            "generated_at": report["generated_at"],
            "source": report["source"],
            "totals": report["totals"],
            "files": files,
            "privacy": {
                "referenced_catalog_only": True,
                "redacted_catalog_fields": CATALOG_REDACTED_FIELDS,
                "contains_project_evidence": True,
            },
        }
        manifest_content = (
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode()
        _write_bundle_file(temporary / "manifest.json", manifest_content)
        os.replace(temporary, destination)
        directory_fd = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination


def build_current_plan_snapshot(report: dict[str, Any]) -> dict[str, Any]:
    if len(report["plans"]) != 1:
        raise ExportError(
            "current-plan snapshot requires exactly one selected plan; "
            f"export selected {len(report['plans'])}"
        )
    exported = report["plans"][0]
    plan = exported["plan"]
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "snapshot_kind": "current_plan_debug",
        "generated_at": report["generated_at"],
        "selection": {
            "plan_id": plan["id"],
            "project_id": plan.get("project_id"),
            "status": plan.get("status"),
            "version": plan.get("version"),
        },
        "source": report["source"],
        "catalog": report["catalog"],
        "performance_comparisons": report["performance_comparisons"],
        "plan": exported,
    }


def resolve_database_path(
    db_path: Path | None,
    orchestrator_home: Path | None,
) -> Path:
    if db_path is not None:
        return db_path
    home = orchestrator_home
    if home is None:
        home = Path(
            os.environ.get(
                "ORCHESTRATOR_HOME",
                str(Path.home() / ".orchestrator"),
            )
        )
    return home / DB_FILENAME


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Export persisted plan runs, telemetry, metrics, insights, and execution "
            "summaries from SQLite without writing to the orchestrator system."
        )
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--db", type=Path, help="Path to an orchestrator SQLite database")
    source.add_argument(
        "--orchestrator-home",
        type=Path,
        help="State directory containing orchestrator.db",
    )
    parser.add_argument(
        "--plan-id",
        action="append",
        dest="plan_ids",
        help="Export only this plan id; repeat to select multiple plans",
    )
    parser.add_argument(
        "--format",
        choices=("json", "bundle"),
        default="json",
        dest="output_format",
        help="Single JSON document or an atomic JSON/JSONL analytics bundle",
    )
    parser.add_argument(
        "--output",
        default="-",
        help="Destination JSON file; defaults to stdout (-)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Parent directory for a timestamped bundle; required for --format bundle",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    db_path = resolve_database_path(args.db, args.orchestrator_home)
    try:
        report = export_database(
            db_path,
            None if args.plan_ids is None else set(args.plan_ids),
        )
        if args.output_format == "bundle":
            if args.output_dir is None:
                raise ExportError("--output-dir is required for --format bundle")
            if args.output != "-":
                raise ExportError("--output is only valid with --format json")
            destination = write_bundle(report, args.output_dir)
            print(destination)
        else:
            if args.output_dir is not None:
                raise ExportError("--output-dir is only valid with --format bundle")
            write_json_report(report, args.output, pretty=args.pretty)
    except (ExportError, OSError, ValueError) as exc:
        print(f"export failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
