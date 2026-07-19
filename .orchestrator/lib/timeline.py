#!/usr/bin/env python3
"""Chronological, human-scannable timeline for one acceleration experiment.

Pure aggregation over events.jsonl written by runtime_wrapper.py -- no LLM
calls. Groups invocation/escalation/verification/outcome events per task so
a report writer doesn't have to re-derive task narratives by hand from the
raw JSONL each time.

Usage:
    python3 .orchestrator/lib/timeline.py <experiment_id>

Prints one JSON array to stdout: one object per task, each with its ordered
list of attempts (runtime, start/end ts, duration, exit/timeout, escalation
reason if any) and final outcome. Does not write any file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNS_ROOT = REPO_ROOT / ".orchestrator" / "runtime-runs"


def load_events(experiment_id: str) -> list[dict[str, Any]]:
    path = RUNS_ROOT / experiment_id / "events.jsonl"
    if not path.exists():
        print(f"error: no events file at {path}", file=sys.stderr)
        sys.exit(1)
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def build_timeline(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    task_ids = list(
        dict.fromkeys(e["task_id"] for e in events if e.get("task_id") is not None)
    )
    tasks = []
    for task_id in task_ids:
        task_events = [e for e in events if e.get("task_id") == task_id]
        routing = next((e for e in task_events if e["event"] == "routing_decision"), None)
        attempts_by_number: dict[int, dict[str, Any]] = {}
        for e in task_events:
            if e["event"] == "invocation_start":
                attempts_by_number.setdefault(e["attempt"], {}).update(
                    {
                        "attempt": e["attempt"],
                        "runtime": e["runtime"],
                        "model": e.get("model"),
                        "started_at": e["ts"],
                    }
                )
            elif e["event"] == "invocation_end":
                attempts_by_number.setdefault(e["attempt"], {}).update(
                    {
                        "ended_at": e["ts"],
                        "exit_code": e.get("exit_code"),
                        "timed_out": e.get("timed_out", False),
                        "duration_seconds": e.get("duration_seconds"),
                    }
                )
            elif e["event"] == "verification":
                attempts_by_number.setdefault(e["attempt"], {}).setdefault(
                    "verification", []
                )
                attempts_by_number[e["attempt"]]["verification"].append(
                    {"exit_code": e.get("exit_code"), "command": e.get("command")}
                )
        escalations = [
            {"from": e["from_runtime"], "to": e["to_runtime"], "reason": e["reason"], "ts": e["ts"]}
            for e in task_events
            if e["event"] == "escalation"
        ]
        outcome = next((e for e in task_events if e["event"] == "task_outcome"), None)
        tasks.append(
            {
                "task_id": task_id,
                "decision_reason": (routing or {}).get("decision_reason"),
                "chosen_runtime": (routing or {}).get("chosen_runtime"),
                "attempts": sorted(attempts_by_number.values(), key=lambda a: a["attempt"]),
                "escalations": escalations,
                "outcome": (
                    {
                        "runtime": outcome["runtime"],
                        "status": outcome["status"],
                        "attempts": outcome["attempts"],
                        "duration_seconds": outcome["duration_seconds"],
                        "quota_consumed": outcome.get("quota_consumed"),
                    }
                    if outcome
                    else None
                ),
            }
        )
    return tasks


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        print("usage: timeline.py <experiment_id>", file=sys.stderr)
        sys.exit(2)
    events = load_events(argv[0])
    print(json.dumps(build_timeline(events), indent=2, sort_keys=False))


if __name__ == "__main__":
    main()
