#!/usr/bin/env python3
"""Single choke point for every delegated-runtime CLI invocation in the
temporary multi-runtime acceleration flow (.claude/skills/accelerate).

Stdlib-only, self-contained. Never call a runtime CLI directly from the
skill or a delegated agent -- every invocation, quota snapshot, routing
decision, verification run, retry/escalation, and outcome goes through the
subcommands below, appended as JSONL to:

    .orchestrator/runtime-runs/<experiment_id>/events.jsonl

Large stdout/stderr are captured to sibling files under
    .orchestrator/runtime-runs/<experiment_id>/logs/<task_id>-a<attempt>.{out,err}
and referenced by path, never inlined.

Unknown/unmeasurable values (quota, cost, tokens) MUST be passed as JSON
null. This module never coerces a missing value to 0 and never estimates
one on the caller's behalf.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNS_ROOT = REPO_ROOT / ".orchestrator" / "runtime-runs"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _experiment_dir(experiment_id: str) -> Path:
    d = RUNS_ROOT / experiment_id
    (d / "logs").mkdir(parents=True, exist_ok=True)
    return d


def _events_path(experiment_id: str) -> Path:
    return _experiment_dir(experiment_id) / "events.jsonl"


def _append_event(experiment_id: str, event: str, fields: dict[str, Any]) -> dict[str, Any]:
    record = {"event": event, "ts": _now(), "experiment_id": experiment_id, **fields}
    with _events_path(experiment_id).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
    return record


def _parse_json_arg(raw: str | None) -> Any:
    if raw is None:
        return None
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def cmd_routing_decision(args: argparse.Namespace) -> None:
    payload = _parse_json_arg(args.json)
    _append_event(
        args.experiment_id,
        "routing_decision",
        {"task_id": args.task_id, **payload},
    )


def cmd_quota_snapshot(args: argparse.Namespace) -> None:
    payload = _parse_json_arg(args.json)
    _append_event(
        args.experiment_id,
        "quota_snapshot",
        {"runtime": args.runtime, **payload},
    )


def cmd_run(args: argparse.Namespace) -> None:
    """Execute one CLI attempt under full capture, stdin always /dev/null."""
    command = _parse_json_arg(args.command_json)
    if not isinstance(command, list) or not all(isinstance(c, str) for c in command):
        print("error: --command-json must be a JSON array of strings", file=sys.stderr)
        sys.exit(2)

    exp_dir = _experiment_dir(args.experiment_id)
    out_path = exp_dir / "logs" / f"{args.task_id}-a{args.attempt}.out"
    err_path = exp_dir / "logs" / f"{args.task_id}-a{args.attempt}.err"

    _append_event(
        args.experiment_id,
        "invocation_start",
        {
            "task_id": args.task_id,
            "attempt": args.attempt,
            "runtime": args.runtime,
            "model": args.model,
            "command": command,
            "cwd": args.workdir,
            "worktree_branch": args.branch,
            "stdout_log": str(out_path.relative_to(REPO_ROOT)),
            "stderr_log": str(err_path.relative_to(REPO_ROOT)),
        },
    )

    start = time.monotonic()
    timed_out = False
    exit_code: int | None
    try:
        with out_path.open("wb") as out_fh, err_path.open("wb") as err_fh:
            proc = subprocess.run(
                command,
                cwd=args.workdir,
                stdin=subprocess.DEVNULL,
                stdout=out_fh,
                stderr=err_fh,
                timeout=args.timeout_seconds,
            )
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        exit_code = None
    duration = time.monotonic() - start

    _append_event(
        args.experiment_id,
        "invocation_end",
        {
            "task_id": args.task_id,
            "attempt": args.attempt,
            "runtime": args.runtime,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "duration_seconds": round(duration, 3),
            "stdout_log": str(out_path.relative_to(REPO_ROOT)),
            "stderr_log": str(err_path.relative_to(REPO_ROOT)),
        },
    )

    tail = out_path.read_text(encoding="utf-8", errors="replace")[-2000:]
    print(
        json.dumps(
            {"exit_code": exit_code, "timed_out": timed_out, "duration_seconds": duration, "stdout_tail": tail}
        )
    )
    sys.exit(exit_code if exit_code is not None else 124)


def cmd_git_change(args: argparse.Namespace) -> None:
    def git(*a: str) -> str:
        r = subprocess.run(["git", "-C", args.repo, *a], capture_output=True, text=True, check=False)
        return r.stdout.strip()

    diff_stat_raw = git("diff", "--stat", args.diff_range)
    numstat_raw = git("diff", "--numstat", args.diff_range)
    files_changed = len([line for line in numstat_raw.splitlines() if line.strip()])
    insertions = deletions = 0
    for line in numstat_raw.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            ins, dele, _ = parts
            insertions += int(ins) if ins.isdigit() else 0
            deletions += int(dele) if dele.isdigit() else 0
    commit_sha = git("rev-parse", "HEAD")

    _append_event(
        args.experiment_id,
        "git_change",
        {
            "task_id": args.task_id,
            "attempt": args.attempt,
            "diffstat": {
                "files_changed": files_changed,
                "insertions": insertions,
                "deletions": deletions,
            },
            "diff_stat_raw": diff_stat_raw,
            "commit_sha": commit_sha,
            "branch": args.branch,
        },
    )


def cmd_verify(args: argparse.Namespace) -> None:
    command = _parse_json_arg(args.command_json)
    start = time.monotonic()
    proc = subprocess.run(
        command, cwd=args.repo, capture_output=True, text=True, timeout=args.timeout_seconds
    )
    duration = time.monotonic() - start
    summary = (proc.stdout + proc.stderr)[-4000:]

    _append_event(
        args.experiment_id,
        "verification",
        {
            "task_id": args.task_id,
            "attempt": args.attempt,
            "command": command,
            "exit_code": proc.returncode,
            "duration_seconds": round(duration, 3),
            "summary": summary,
        },
    )
    print(json.dumps({"exit_code": proc.returncode, "summary": summary}))
    sys.exit(proc.returncode)


def cmd_retry(args: argparse.Namespace) -> None:
    _append_event(
        args.experiment_id,
        "retry",
        {
            "task_id": args.task_id,
            "from_runtime": args.from_runtime,
            "to_runtime": args.to_runtime,
            "reason": args.reason,
        },
    )


def cmd_escalation(args: argparse.Namespace) -> None:
    _append_event(
        args.experiment_id,
        "escalation",
        {
            "task_id": args.task_id,
            "from_runtime": args.from_runtime,
            "to_runtime": args.to_runtime,
            "reason": args.reason,
        },
    )


def cmd_human_intervention(args: argparse.Namespace) -> None:
    _append_event(
        args.experiment_id,
        "human_intervention",
        {"task_id": args.task_id, "description": args.description},
    )


def cmd_task_outcome(args: argparse.Namespace) -> None:
    quota_consumed = _parse_json_arg(args.quota_consumed_json)
    _append_event(
        args.experiment_id,
        "task_outcome",
        {
            "task_id": args.task_id,
            "runtime": args.runtime,
            "status": args.status,
            "attempts": args.attempts,
            "duration_seconds": args.duration_seconds,
            "quota_consumed": quota_consumed,
        },
    )


# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="subcommand", required=True)

    def add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--experiment-id", required=True)
        sp.add_argument("--task-id", required=True)

    sp = sub.add_parser("routing-decision")
    add_common(sp)
    sp.add_argument("--json", required=True, help="JSON object: candidates, chosen_runtime, decision_reason, packet")
    sp.set_defaults(func=cmd_routing_decision)

    sp = sub.add_parser("quota-snapshot")
    sp.add_argument("--experiment-id", required=True)
    sp.add_argument("--task-id", default=None)
    sp.add_argument("--runtime", required=True)
    sp.add_argument("--json", required=True, help="JSON object: quota_remaining_percent, confidence, source, note")
    sp.set_defaults(func=cmd_quota_snapshot)

    sp = sub.add_parser("run", help="Execute one CLI attempt under capture")
    add_common(sp)
    sp.add_argument("--attempt", type=int, required=True)
    sp.add_argument("--runtime", required=True)
    sp.add_argument("--model", default=None)
    sp.add_argument("--workdir", required=True)
    sp.add_argument("--branch", default=None)
    sp.add_argument("--command-json", required=True)
    sp.add_argument("--timeout-seconds", type=int, default=900)
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("git-change")
    add_common(sp)
    sp.add_argument("--attempt", type=int, required=True)
    sp.add_argument("--repo", required=True)
    sp.add_argument("--branch", default=None)
    sp.add_argument("--diff-range", default="HEAD")
    sp.set_defaults(func=cmd_git_change)

    sp = sub.add_parser("verify")
    add_common(sp)
    sp.add_argument("--attempt", type=int, required=True)
    sp.add_argument("--repo", required=True)
    sp.add_argument("--command-json", required=True, help='JSON array, e.g. ["make","check"]')
    sp.add_argument("--timeout-seconds", type=int, default=1800)
    sp.set_defaults(func=cmd_verify)

    sp = sub.add_parser("retry")
    add_common(sp)
    sp.add_argument("--from-runtime", required=True)
    sp.add_argument("--to-runtime", required=True)
    sp.add_argument("--reason", required=True)
    sp.set_defaults(func=cmd_retry)

    sp = sub.add_parser("escalation")
    add_common(sp)
    sp.add_argument("--from-runtime", required=True)
    sp.add_argument("--to-runtime", required=True)
    sp.add_argument("--reason", required=True)
    sp.set_defaults(func=cmd_escalation)

    sp = sub.add_parser("human-intervention")
    add_common(sp)
    sp.add_argument("--description", required=True)
    sp.set_defaults(func=cmd_human_intervention)

    sp = sub.add_parser("task-outcome")
    add_common(sp)
    sp.add_argument("--runtime", required=True)
    sp.add_argument("--status", required=True, choices=["verified_success", "failed", "escalated_unresolved"])
    sp.add_argument("--attempts", type=int, required=True)
    sp.add_argument("--duration-seconds", type=float, required=True)
    sp.add_argument(
        "--quota-consumed-json",
        required=True,
        help='JSON object with numeric-or-null fields, e.g. {"tokens":12519,"cost_usd":null,"status":"partial"}',
    )
    sp.set_defaults(func=cmd_task_outcome)

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
