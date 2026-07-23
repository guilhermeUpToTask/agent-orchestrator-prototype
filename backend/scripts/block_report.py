#!/usr/bin/env python3
"""
scripts/block_report.py — Frequency report of PlanBlock kinds/stages (read-only).

Standalone, stdlib-only SQLite reader for offline block-frequency measurement.
Never constructs the application container, imports domain/runtime code, calls
an API, or writes to the orchestrator database. Opens SQLite with ``mode=ro``
and ``query_only=ON``; all reads happen in one snapshot transaction.

Scans every plan row's JSON document and collects PlanBlock objects from the
plan-wide scalar ``block`` and the per-goal dict ``goal_blocks`` (both active
and resolved). Emits totals, a (kind, stage) breakdown, per-plan counts, and
task_id repeat offenders.

Usage (from backend/):

    python scripts/block_report.py --pretty
    python scripts/block_report.py --db /path/to/orchestrator.db
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

SCHEMA_VERSION = "1.0"
DB_FILENAME = "orchestrator.db"
REQUIRED_TABLES = frozenset({"plans"})


class ReportError(RuntimeError):
    """The requested database cannot produce a trustworthy block report."""


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


def _json_value(raw: object) -> Any:
    if raw is None:
        return None
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None


def _is_active(block: dict[str, Any]) -> bool:
    """Active when unresolved. ``resolved_at is None`` is the PlanBlock authority."""
    if "resolved_at" in block:
        return block["resolved_at"] is None
    if "active" in block:
        return bool(block["active"])
    return True


def _collect_blocks(
    plan_id: str,
    aggregate: Any,
) -> list[dict[str, Any]]:
    """Extract PlanBlock dicts from plan-wide ``block`` and ``goal_blocks``."""
    if not isinstance(aggregate, dict):
        return []
    collected: list[dict[str, Any]] = []

    plan_block = aggregate.get("block")
    if isinstance(plan_block, dict) and plan_block.get("kind") is not None:
        collected.append(
            {
                "plan_id": plan_id,
                "source": "block",
                "block": plan_block,
            }
        )

    goal_blocks = aggregate.get("goal_blocks")
    if isinstance(goal_blocks, dict):
        for goal_key, goal_block in goal_blocks.items():
            if not isinstance(goal_block, dict) or goal_block.get("kind") is None:
                continue
            collected.append(
                {
                    "plan_id": plan_id,
                    "source": "goal_blocks",
                    "goal_key": str(goal_key),
                    "block": goal_block,
                }
            )
    return collected


def build_block_report(db_path: Path) -> dict[str, Any]:
    """Return one consistent read-only block frequency report."""
    resolved = db_path.expanduser().resolve()
    if not resolved.is_file():
        raise ReportError(f"database does not exist: {resolved}")
    uri = f"{resolved.as_uri()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    except sqlite3.Error as exc:
        raise ReportError(f"could not open database read-only: {exc}") from exc
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        if int(connection.execute("PRAGMA query_only").fetchone()[0]) != 1:
            raise ReportError("SQLite did not enable query_only mode")
        connection.execute("BEGIN")
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        missing = sorted(REQUIRED_TABLES - tables)
        if missing:
            raise ReportError(
                "database schema is missing required tables; run migrations first: "
                + ", ".join(missing)
            )

        plan_rows = connection.execute(
            "SELECT id, data FROM plans ORDER BY created_at, id"
        ).fetchall()
        all_entries: list[dict[str, Any]] = []
        for row in plan_rows:
            plan_id = str(row["id"])
            aggregate = _json_value(row["data"])
            all_entries.extend(_collect_blocks(plan_id, aggregate))
        connection.rollback()
    except sqlite3.Error as exc:
        raise ReportError(f"database report failed: {exc}") from exc
    finally:
        connection.close()

    return _summarize(resolved, len(plan_rows), all_entries)


def _summarize(
    database: Path,
    plans_scanned: int,
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    active_count = 0
    resolved_count = 0
    by_kind_stage: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"total": 0, "active": 0, "resolved": 0}
    )
    per_plan: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "active": 0, "resolved": 0}
    )
    task_counter: Counter[str] = Counter()
    task_plans: dict[str, set[str]] = defaultdict(set)

    for entry in entries:
        block = entry["block"]
        plan_id = str(entry["plan_id"])
        kind = str(block.get("kind") or "unknown")
        stage = str(block.get("stage") or "unknown")
        is_active = _is_active(block)
        status_key = "active" if is_active else "resolved"

        if is_active:
            active_count += 1
        else:
            resolved_count += 1

        bucket = by_kind_stage[(kind, stage)]
        bucket["total"] += 1
        bucket[status_key] += 1

        plan_bucket = per_plan[plan_id]
        plan_bucket["total"] += 1
        plan_bucket[status_key] += 1

        task_id = block.get("task_id")
        if isinstance(task_id, str) and task_id:
            task_counter[task_id] += 1
            task_plans[task_id].add(plan_id)

    by_kind_stage_rows = [
        {
            "kind": kind,
            "stage": stage,
            "total": counts["total"],
            "active": counts["active"],
            "resolved": counts["resolved"],
        }
        for (kind, stage), counts in sorted(
            by_kind_stage.items(),
            key=lambda item: (-item[1]["total"], item[0][0], item[0][1]),
        )
    ]
    per_plan_rows = [
        {
            "plan_id": plan_id,
            "total": counts["total"],
            "active": counts["active"],
            "resolved": counts["resolved"],
        }
        for plan_id, counts in sorted(
            per_plan.items(),
            key=lambda item: (-item[1]["total"], item[0]),
        )
    ]
    # Same task_id blocking more than once (across plans or within one plan).
    task_repeat_offenders = [
        {
            "task_id": task_id,
            "count": count,
            "plan_ids": sorted(task_plans[task_id]),
        }
        for task_id, count in sorted(
            task_counter.items(),
            key=lambda item: (-item[1], item[0]),
        )
        if count > 1
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "database": str(database),
            "read_only": True,
            "snapshot": "single SQLite read transaction",
        },
        "totals": {
            "plans_scanned": plans_scanned,
            "blocks": len(entries),
            "active": active_count,
            "resolved": resolved_count,
        },
        "by_kind_stage": by_kind_stage_rows,
        "per_plan": per_plan_rows,
        "task_repeat_offenders": task_repeat_offenders,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Report PlanBlock frequency by kind/stage from persisted plan JSON "
            "without writing to the orchestrator system."
        )
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--db", type=Path, help="Path to an orchestrator SQLite database")
    source.add_argument(
        "--orchestrator-home",
        type=Path,
        help="State directory containing orchestrator.db",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    db_path = resolve_database_path(args.db, args.orchestrator_home)
    try:
        report = build_block_report(db_path)
        indent = 2 if args.pretty else None
        sys.stdout.write(
            json.dumps(report, ensure_ascii=False, indent=indent, sort_keys=bool(args.pretty))
            + "\n"
        )
    except (ReportError, OSError, ValueError) as exc:
        print(f"block report failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
