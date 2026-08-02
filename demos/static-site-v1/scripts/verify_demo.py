#!/usr/bin/env python3
"""Structural verification for a demo run.

A demo cannot assert what a fixture asserts. A real reasoner decomposes the
same brief differently every run — three goals one time, four the next — so
"expect three goals" would fail a system that is working correctly, which is
worse than asserting nothing.

What it checks instead are the properties that hold NO MATTER HOW the work was
decomposed. Every one of these is a promise the orchestrator makes about any
cycle, so a violation is a real defect rather than a variance:

  1. every goal in the cycle reached DONE and was promoted
  2. every commit SHA the API served actually resolves in git
  3. the default branch is byte-identical to the seed tag
  4. no goal was merged without accepted, revision-bound evidence
  5. the disposition was recorded with an output reference
  6. the root plan returned to idle

Exit codes follow the fixture convention, and the distinction is load-bearing:

  0  every check passed
  1  a check FAILED — a real finding about the orchestrator
  2  the harness is broken (no server, bad ids, unreadable repo) — NOT a finding

Conflating 1 and 2 is how a broken harness gets published as a defect, or a
defect gets dismissed as a broken harness.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

OK = "PASS"
BAD = "FAIL"


class HarnessError(RuntimeError):
    """Exit 2: we could not check, which is not the same as a failed check."""


def _get(base: str, path: str, token: str | None) -> Any:
    request = urllib.request.Request(f"{base}{path}")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise HarnessError(f"GET {path} -> HTTP {exc.code}: {exc.read()[:300]!r}") from exc
    except OSError as exc:
        raise HarnessError(f"GET {path} failed: {exc}") from exc


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        raise HarnessError(f"git {' '.join(args[:2])} failed: {result.stderr.strip()[:200]}")
    return result.stdout.strip()


def _resolves(repo: Path, sha: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


class Checks:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool, str]] = []

    def record(self, name: str, passed: bool, detail: str = "") -> None:
        self.results.append((name, passed, detail))

    @property
    def failed(self) -> list[tuple[str, bool, str]]:
        return [item for item in self.results if not item[1]]

    def report(self) -> int:
        width = max(len(name) for name, _, _ in self.results)
        for name, passed, detail in self.results:
            mark = OK if passed else BAD
            line = f"  [{mark}] {name.ljust(width)}"
            if detail:
                line += f"  {detail}"
            print(line)
        total = len(self.results)
        passed = total - len(self.failed)
        print(f"\n{passed}/{total} structural checks passed")
        return 0 if not self.failed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--token", default=None)
    parser.add_argument("--plan-id", required=True)
    parser.add_argument("--cycle-id", required=True)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument(
        "--seed-tag",
        default="demo-seed",
        help="the tag the default branch must still equal",
    )
    args = parser.parse_args()

    checks = Checks()

    plan = _get(args.base, f"/api/plans/{args.plan_id}", args.token)
    evidence = _get(
        args.base,
        f"/api/plans/{args.plan_id}/cycles/{args.cycle_id}/evidence",
        args.token,
    )
    review = _get(
        args.base,
        f"/api/plans/{args.plan_id}/cycles/{args.cycle_id}/review",
        args.token,
    )

    if not args.repo.exists():
        raise HarnessError(f"repository {args.repo} does not exist")

    goals = evidence.get("goals", [])
    if not goals:
        raise HarnessError("the cycle evidence document reports no goals at all")

    # 1 — every goal promoted.
    unpromoted = [g["goal_id"] for g in goals if not g.get("promotion")]
    checks.record(
        "every goal was promoted",
        not unpromoted,
        f"{len(goals)} goals" if not unpromoted else f"missing: {unpromoted}",
    )

    # 2 — every served SHA resolves in git. The evidence document's central
    # promise: its references are real, not decorative.
    served: list[str] = []
    for goal in goals:
        promotion = goal.get("promotion")
        if promotion:
            served.append(promotion["merge_sha"])
        for task in goal.get("tasks", []):
            for item in task.get("accepted_evidence", []):
                served += [item["candidate_commit_sha"], item["test_commit_sha"]]
    unresolved = sorted({sha for sha in served if sha and not _resolves(args.repo, sha)})
    checks.record(
        "every served SHA resolves in git",
        not unresolved,
        f"{len(set(served))} distinct" if not unresolved else f"missing: {unresolved[:4]}",
    )

    # 3 — the default branch is untouched. The guarantee the whole branch
    # ladder exists to make.
    try:
        seed_sha = _git(args.repo, "rev-parse", f"{args.seed_tag}^{{commit}}")
        default_branch = evidence.get("delivery", {}).get("default_branch") or "main"
        head_sha = _git(args.repo, "rev-parse", f"{default_branch}^{{commit}}")
        checks.record(
            "default branch is byte-identical to the seed",
            seed_sha == head_sha,
            f"{default_branch} @ {head_sha[:8]}",
        )
    except HarnessError as exc:
        checks.record("default branch is byte-identical to the seed", False, str(exc))

    # 4 — nothing merged without accepted, revision-bound evidence.
    without_evidence = [
        f"{goal['goal_id'][:8]}/{task['task_id'][:8]}"
        for goal in goals
        if goal.get("promotion")
        for task in goal.get("tasks", [])
        if task["status"] == "done" and not task.get("accepted_evidence")
    ]
    checks.record(
        "no goal merged without accepted evidence",
        not without_evidence,
        "" if not without_evidence else f"bare: {without_evidence}",
    )

    # 5 — a disposition was recorded with a reference.
    disposition = evidence.get("disposition") or {}
    has_reference = bool(disposition.get("output_reference")) or disposition.get(
        "disposition"
    ) == "discard"
    checks.record(
        "disposition recorded with an output reference",
        bool(disposition.get("disposition")) and has_reference,
        f"{disposition.get('disposition')} -> {disposition.get('output_reference')}",
    )

    # 6 — the root returned to idle. The cyclic root is never terminal, so
    # "finished" means idle and available, not done.
    checks.record(
        "root plan returned to idle",
        plan.get("status") == "idle",
        f"status={plan.get('status')}",
    )

    # 7 — the review surface can actually split this cycle. Not a promise the
    # orchestrator makes about every cycle, but the demo's own claim, so it is
    # checked here rather than asserted in prose.
    units = [
        unit
        for goal in review.get("goals", [])
        for task in goal.get("tasks", [])
        for unit in task.get("units", [])
    ]
    resolved_units = [u for u in units if u.get("resolved")]
    checks.record(
        "the review surface split the cycle into units",
        len(resolved_units) >= 2,
        f"{len(resolved_units)} reviewable units",
    )

    print(f"\nDemo: static-site-v1   plan={args.plan_id[:8]}  cycle={args.cycle_id[:8]}")
    print(f"Goals: {len(goals)}   (a real reasoner decomposes differently every run)\n")
    return checks.report()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except HarnessError as exc:
        print(f"[HARNESS] {exc}", file=sys.stderr)
        print(
            "\nExit 2: the checker could not run. This is NOT a finding about "
            "the orchestrator.",
            file=sys.stderr,
        )
        sys.exit(2)
