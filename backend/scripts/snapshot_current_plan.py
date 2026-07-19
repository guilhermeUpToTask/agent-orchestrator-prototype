#!/usr/bin/env python3
"""Write one focused current-plan debugging snapshot from persisted SQLite state."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from export_plan_runs import (
    ExportError,
    build_current_plan_snapshot,
    export_database,
    resolve_database_path,
    write_json_report,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Export one current plan and its persisted debugging evidence without "
            "writing to the orchestrator system."
        )
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--db", type=Path, help="Path to an orchestrator SQLite database")
    source.add_argument(
        "--orchestrator-home",
        type=Path,
        help="State directory containing orchestrator.db",
    )
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument("--plan-id", help="Snapshot this exact plan")
    selector.add_argument(
        "--project-id",
        help="Snapshot the unique plan owned by this project",
    )
    parser.add_argument(
        "--output",
        default="-",
        help="Destination JSON file; defaults to stdout (-)",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    db_path = resolve_database_path(args.db, args.orchestrator_home)
    try:
        report = export_database(
            db_path,
            {args.plan_id} if args.plan_id is not None else None,
            project_id=args.project_id,
            current=args.plan_id is None and args.project_id is None,
        )
        snapshot = build_current_plan_snapshot(report)
        write_json_report(snapshot, args.output, pretty=args.pretty)
    except (ExportError, OSError, ValueError) as exc:
        print(f"snapshot failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
