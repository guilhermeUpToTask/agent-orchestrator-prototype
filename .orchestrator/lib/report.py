#!/usr/bin/env python3
"""Deterministic per-runtime comparison report for one acceleration
experiment. Pure aggregation over events.jsonl written by
runtime_wrapper.py -- no LLM calls, no inference.

Usage:
    python3 .orchestrator/lib/report.py <experiment_id>

Writes:
    .orchestrator/runtime-runs/<experiment_id>/report.json
    .orchestrator/runtime-runs/<experiment_id>/report.md

Ranking is by verified task completions, then first-pass success rate.
Raw token/output volume is reported but never used to rank. Any metric
whose inputs include a null value is reported as "unavailable" rather than
silently summed as if complete.
"""

from __future__ import annotations

import json
import statistics
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
    events = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def _p90(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = min(len(s) - 1, int(round(0.9 * (len(s) - 1))))
    return s[idx]


def _p90_rounded(values: list[float]) -> float | None:
    p90 = _p90(values)
    return round(p90, 1) if p90 is not None else None


def build_report(events: list[dict[str, Any]]) -> dict[str, Any]:
    task_outcomes = [e for e in events if e["event"] == "task_outcome"]
    escalations = [e for e in events if e["event"] == "escalation"]
    human_interventions = [e for e in events if e["event"] == "human_intervention"]
    invocation_starts = [e for e in events if e["event"] == "invocation_start"]

    # initial runtime per task = runtime of the attempt==1 invocation_start
    initial_runtime_by_task: dict[str, str] = {}
    for e in invocation_starts:
        if e.get("attempt") == 1:
            initial_runtime_by_task.setdefault(e["task_id"], e["runtime"])

    runtimes = sorted(
        {e["runtime"] for e in task_outcomes}
        | {e["runtime"] for e in invocation_starts}
        | {e["from_runtime"] for e in escalations}
        | {e["to_runtime"] for e in escalations}
    )

    per_runtime: dict[str, dict[str, Any]] = {}
    for runtime in runtimes:
        outcomes_final = [e for e in task_outcomes if e["runtime"] == runtime]
        tasks_initial = [t for t, r in initial_runtime_by_task.items() if r == runtime]

        verified_completions = sum(1 for e in outcomes_final if e["status"] == "verified_success")

        first_pass_success = sum(
            1
            for e in outcomes_final
            if e["status"] == "verified_success"
            and e["attempts"] == 1
            and initial_runtime_by_task.get(e["task_id"]) == runtime
        )
        first_pass_denominator = len(tasks_initial)
        first_pass_success_rate = (
            round(first_pass_success / first_pass_denominator, 3) if first_pass_denominator else None
        )

        durations = [e["duration_seconds"] for e in outcomes_final if e.get("duration_seconds") is not None]

        escalations_from = [e for e in escalations if e["from_runtime"] == runtime]
        escalation_rate = (
            round(len(escalations_from) / first_pass_denominator, 3) if first_pass_denominator else None
        )

        human_intervention_count = sum(
            1
            for hi in human_interventions
            if initial_runtime_by_task.get(str(hi.get("task_id"))) == runtime
        )

        tokens_vals = [
            e["quota_consumed"].get("tokens")
            for e in outcomes_final
            if isinstance(e.get("quota_consumed"), dict)
        ]
        cost_vals = [
            e["quota_consumed"].get("cost_usd")
            for e in outcomes_final
            if isinstance(e.get("quota_consumed"), dict)
        ]
        tokens_total = None if (not tokens_vals or any(v is None for v in tokens_vals)) else sum(tokens_vals)
        cost_total = None if (not cost_vals or any(v is None for v in cost_vals)) else sum(cost_vals)

        per_runtime[runtime] = {
            "runtime": runtime,
            "tasks_routed_initially": first_pass_denominator,
            "verified_completions": verified_completions,
            "first_pass_success_rate": first_pass_success_rate,
            "duration_seconds_median": round(statistics.median(durations), 1) if durations else None,
            "duration_seconds_p90": _p90_rounded(durations),
            "escalation_rate": escalation_rate,
            "human_interventions": human_intervention_count,
            "quota_tokens_total": tokens_total if tokens_total is not None else "unavailable",
            "estimated_cost_usd": cost_total if cost_total is not None else "unavailable",
        }

    ranked = sorted(
        per_runtime.values(),
        key=lambda r: (
            -r["verified_completions"],
            -(r["first_pass_success_rate"] or 0.0),
        ),
    )

    return {"runtimes": ranked, "task_count": len(initial_runtime_by_task)}


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Runtime comparison report",
        "",
        f"Tasks observed: {report['task_count']}",
        "",
        "Ranked by verified completions, then first-pass success rate. "
        "Token/cost figures are reported, never used to rank; `unavailable` "
        "means at least one contributing value was null, not zero.",
        "",
        "| Runtime | Routed | Verified | First-pass success | Duration median (s) | "
        "Duration p90 (s) | Escalation rate | Human interventions | Tokens | Est. cost (USD) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in report["runtimes"]:
        lines.append(
            "| {runtime} | {tasks_routed_initially} | {verified_completions} | "
            "{first_pass_success_rate} | {duration_seconds_median} | {duration_seconds_p90} | "
            "{escalation_rate} | {human_interventions} | {quota_tokens_total} | "
            "{estimated_cost_usd} |".format(**r)
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        print("usage: report.py <experiment_id>", file=sys.stderr)
        sys.exit(2)
    experiment_id = argv[0]
    events = load_events(experiment_id)
    report = build_report(events)

    out_dir = RUNS_ROOT / experiment_id
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    (out_dir / "report.md").write_text(to_markdown(report), encoding="utf-8")
    print(to_markdown(report))


if __name__ == "__main__":
    main()
