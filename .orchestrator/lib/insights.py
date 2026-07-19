#!/usr/bin/env python3
"""Cross-experiment reliability rollup for the /accelerate runtime pool.

report.py compares runtimes within ONE experiment. This aggregates every
.orchestrator/runtime-runs/<experiment_id>/events.jsonl this repo has ever
recorded into durable per-runtime stats, so the NEXT /accelerate run can
route on accumulated history instead of just the one-shot probe snapshot
recorded in runtime-pool.yaml.

Pure aggregation over already-captured evidence -- no LLM calls, no
inference, no invented numbers. A metric whose inputs include a null value
is reported as "unavailable", never coerced to zero. Thresholds below flag
patterns for a human/coordinator to interpret; they do not themselves alter
routing policy (see SKILL.md: "do not modify routing policy silently").

Usage:
    python3 .orchestrator/lib/insights.py

Writes:
    .orchestrator/insights.md
    .orchestrator/insights.json
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNS_ROOT = REPO_ROOT / ".orchestrator" / "runtime-runs"
OUT_MD = REPO_ROOT / ".orchestrator" / "insights.md"
OUT_JSON = REPO_ROOT / ".orchestrator" / "insights.json"

# Flag thresholds -- descriptive triggers, not routing decisions.
ESCALATION_RATE_FLAG = 0.3
LOW_SAMPLE_SIZE = 3


def load_all_events() -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not RUNS_ROOT.exists():
        return events
    for events_path in sorted(RUNS_ROOT.glob("*/events.jsonl")):
        experiment_id = events_path.parent.name
        with events_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                record.setdefault("experiment_id", experiment_id)
                events.append(record)
    return events


def _p90(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = min(len(s) - 1, int(round(0.9 * (len(s) - 1))))
    return round(s[idx], 1)


def build_insights(events: list[dict[str, Any]]) -> dict[str, Any]:
    routing_decisions = [e for e in events if e["event"] == "routing_decision"]
    task_outcomes = [e for e in events if e["event"] == "task_outcome"]
    invocation_starts = [e for e in events if e["event"] == "invocation_start"]
    escalations = [e for e in events if e["event"] == "escalation"]
    retries = [e for e in events if e["event"] == "retry"]
    human_interventions = [e for e in events if e["event"] == "human_intervention"]

    # task_id -> (chosen_runtime, risk) from its first routing decision.
    routing_by_task: dict[str, dict[str, Any]] = {}
    for e in routing_decisions:
        task_id = e.get("task_id")
        if task_id and task_id not in routing_by_task:
            packet = e.get("packet") or {}
            routing_by_task[task_id] = {
                "chosen_runtime": e.get("chosen_runtime"),
                "risk": packet.get("risk"),
                "objective": packet.get("objective"),
            }

    initial_runtime_by_task: dict[str, str] = {}
    for e in invocation_starts:
        if e.get("attempt") == 1:
            initial_runtime_by_task.setdefault(e["task_id"], e["runtime"])

    runtimes = sorted(
        {e["runtime"] for e in task_outcomes}
        | {e["runtime"] for e in invocation_starts}
        | {r["chosen_runtime"] for r in routing_by_task.values() if r["chosen_runtime"]}
    )

    per_runtime: dict[str, dict[str, Any]] = {}
    flags: list[str] = []

    for runtime in runtimes:
        outcomes = [e for e in task_outcomes if e["runtime"] == runtime]
        tasks_initial = [t for t, r in initial_runtime_by_task.items() if r == runtime]
        routed_count = sum(1 for r in routing_by_task.values() if r["chosen_runtime"] == runtime)

        verified = sum(1 for e in outcomes if e["status"] == "verified_success")
        failed = sum(1 for e in outcomes if e["status"] == "failed")
        escalated_unresolved = sum(1 for e in outcomes if e["status"] == "escalated_unresolved")

        first_pass_success = sum(
            1
            for e in outcomes
            if e["status"] == "verified_success"
            and e["attempts"] == 1
            and initial_runtime_by_task.get(e["task_id"]) == runtime
        )
        denom = len(tasks_initial)
        first_pass_rate = round(first_pass_success / denom, 3) if denom else None

        durations = [e["duration_seconds"] for e in outcomes if e.get("duration_seconds") is not None]

        esc_from = [e for e in escalations if e["from_runtime"] == runtime]
        esc_rate = round(len(esc_from) / denom, 3) if denom else None
        esc_reasons = Counter(e.get("reason", "unspecified") for e in esc_from)

        retry_from = [e for e in retries if e["from_runtime"] == runtime]
        retry_reasons = Counter(e.get("reason", "unspecified") for e in retry_from)

        hi_count = sum(
            1 for hi in human_interventions if initial_runtime_by_task.get(str(hi.get("task_id"))) == runtime
        )

        # Success rate broken down by declared task risk -- tells you whether
        # a runtime is reliable in general or only on low-risk work.
        by_risk: dict[str, dict[str, Any]] = {}
        for risk in ("low", "medium", "high", "critical"):
            risk_tasks = {t for t, r in routing_by_task.items() if r["chosen_runtime"] == runtime and r["risk"] == risk}
            if not risk_tasks:
                continue
            risk_verified = sum(1 for e in outcomes if e["task_id"] in risk_tasks and e["status"] == "verified_success")
            by_risk[risk] = {
                "routed": len(risk_tasks),
                "verified": risk_verified,
                "verified_rate": round(risk_verified / len(risk_tasks), 3),
            }

        per_runtime[runtime] = {
            "runtime": runtime,
            "tasks_routed": routed_count,
            "verified_completions": verified,
            "failed": failed,
            "escalated_unresolved": escalated_unresolved,
            "first_pass_success_rate": first_pass_rate,
            "duration_seconds_median": round(statistics.median(durations), 1) if durations else None,
            "duration_seconds_p90": _p90(durations),
            "escalation_rate": esc_rate,
            "escalation_reasons": dict(esc_reasons.most_common(5)),
            "retry_reasons": dict(retry_reasons.most_common(5)),
            "human_interventions": hi_count,
            "by_risk": by_risk,
            "sample_size": denom,
        }

        if denom >= 1 and verified == 0:
            flags.append(f"{runtime}: routed {denom} task(s), verified zero -- investigate before routing more.")
        if esc_rate is not None and esc_rate > ESCALATION_RATE_FLAG:
            flags.append(f"{runtime}: escalation rate {esc_rate:.0%} exceeds {ESCALATION_RATE_FLAG:.0%} flag threshold.")
        if hi_count > 0:
            flags.append(f"{runtime}: {hi_count} human intervention(s) recorded -- check events for root cause.")
        if 0 < denom < LOW_SAMPLE_SIZE:
            flags.append(f"{runtime}: only {denom} sample(s) -- treat its rates as low-confidence.")

    ranked = sorted(
        per_runtime.values(),
        key=lambda r: (-r["verified_completions"], -(r["first_pass_success_rate"] or 0.0)),
    )

    experiments = sorted({e["experiment_id"] for e in events})
    return {
        "experiments_observed": experiments,
        "task_count": len(routing_by_task),
        "runtimes": ranked,
        "flags": flags,
    }


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Cross-experiment runtime insights",
        "",
        f"Experiments observed: {', '.join(report['experiments_observed']) or '(none)'}",
        f"Tasks observed (lifetime): {report['task_count']}",
        "",
        "Pure aggregation over `.orchestrator/runtime-runs/*/events.jsonl` -- no inference. "
        "Regenerate after every experiment via `python3 .orchestrator/lib/insights.py`. "
        "Consult this during the /accelerate Routing step; do not let it silently override "
        "the manifest -- surface disagreements to the user instead.",
        "",
    ]
    if report["flags"]:
        lines.append("## Flags")
        lines.append("")
        for flag in report["flags"]:
            lines.append(f"- {flag}")
        lines.append("")

    lines += [
        "## Per-runtime lifetime stats",
        "",
        "| Runtime | Routed | Verified | Failed | Escalated (unresolved) | First-pass success | "
        "Duration median (s) | Duration p90 (s) | Escalation rate | Human interventions |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in report["runtimes"]:
        lines.append(
            "| {runtime} | {tasks_routed} | {verified_completions} | {failed} | "
            "{escalated_unresolved} | {first_pass_success_rate} | {duration_seconds_median} | "
            "{duration_seconds_p90} | {escalation_rate} | {human_interventions} |".format(**r)
        )
    lines.append("")

    lines.append("## By declared risk level")
    lines.append("")
    for r in report["runtimes"]:
        if not r["by_risk"]:
            continue
        lines.append(f"**{r['runtime']}**")
        for risk, stats in r["by_risk"].items():
            lines.append(f"- {risk}: {stats['verified']}/{stats['routed']} verified ({stats['verified_rate']:.0%})")
        lines.append("")

    lines.append("## Escalation / retry reasons observed")
    lines.append("")
    for r in report["runtimes"]:
        if not r["escalation_reasons"] and not r["retry_reasons"]:
            continue
        lines.append(f"**{r['runtime']}**")
        for reason, count in r["escalation_reasons"].items():
            lines.append(f"- escalated x{count}: {reason}")
        for reason, count in r["retry_reasons"].items():
            lines.append(f"- retried x{count}: {reason}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    if argv:
        print("usage: insights.py  (no arguments -- aggregates every experiment)", file=sys.stderr)
        sys.exit(2)
    events = load_all_events()
    report = build_insights(events)
    OUT_JSON.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    OUT_MD.write_text(to_markdown(report), encoding="utf-8")
    print(to_markdown(report))


if __name__ == "__main__":
    main()
