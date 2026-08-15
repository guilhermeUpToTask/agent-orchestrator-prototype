#!/usr/bin/env python3
"""Verify one first-cycle-v1 run from served facts and the git repository.

The run script prints a green tick when the API stops refusing it. That is not
the same as a run that did what it claimed, so this asks the questions the
walkthrough exists to answer:

  1. the cycle reached publication and a disposition was recorded
  2. every task carries accepted, revision-bound verification evidence
  3. the goal branch really merged into the cycle branch at the SHA the API
     serves — read from `GET .../evidence`, never reconstructed from a naming
     convention
  4. the seed repository's default branch is byte-identical to the seed tag
  5. the plan settled IDLE with no human block outstanding
  6. the run stayed inside its size budget (a reasoner that plans a platform
     rewrite from this brief is a finding, not a success)

    ./scripts/verify_run.py --plan PLAN_ID --repo /path/to/repo
    ./scripts/verify_run.py --plan PLAN_ID --repo … --tier 1   # + the real check

Exit 0 when every check passes, 1 when any fails, 2 when the facts could not be
collected — a failed check and a broken harness are different findings and must
not share an exit code.

Reads only. Nothing here mutates a plan, a branch, or the database.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

SEED_TAG = "first-cycle-v1-seed"
SEED_IMPLEMENTATION = "src/first_cycle/slug.py"

# EXPECTATIONS.md size budget. The brief asks for one goal; two is tolerated,
# three means the reasoner did not hear the constraint and pushing through is
# how a fixture quietly stops being one.
MAX_GOALS = 2
MAX_TASKS_PER_GOAL = 3


class CollectionError(RuntimeError):
    """The harness could not gather the facts — distinct from a failed check."""


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    expectation: int


# ── collection ───────────────────────────────────────────────────────────────


def _get(base: str, path: str, token: str | None) -> Any:
    request = urllib.request.Request(f"{base}{path}")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:  # noqa: PERF203 - one call, one message
        raise CollectionError(f"GET {path} -> HTTP {exc.code}") from exc
    except OSError as exc:
        raise CollectionError(f"GET {path} failed: {exc}") from exc


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise CollectionError(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout.strip()


# ── judgement (pure over plain dicts, so it is testable without a server) ─────


def evaluate_plan(plan: dict[str, Any], evidence: dict[str, Any]) -> list[Check]:
    checks: list[Check] = []

    checks.append(
        Check(
            "plan settled idle",
            plan.get("status") == "idle",
            f"status={plan.get('status')} activity={plan.get('activity')}",
            5,
        )
    )

    open_blocks = [
        block
        for block in [plan.get("block"), *(plan.get("goal_blocks") or {}).values()]
        if block
    ]
    checks.append(
        Check(
            "no block outstanding",
            not open_blocks,
            "; ".join(b.get("explanation", "?") for b in open_blocks) or "none",
            5,
        )
    )

    disposition = evidence.get("disposition") or {}
    checks.append(
        Check(
            "output disposition recorded",
            bool(disposition.get("disposition")),
            f"{disposition.get('disposition')} -> {disposition.get('output_reference')}",
            1,
        )
    )

    goals = evidence.get("goals") or []
    checks.append(
        Check(
            "size budget: goals",
            0 < len(goals) <= MAX_GOALS,
            f"{len(goals)} goal(s), budget {MAX_GOALS}",
            6,
        )
    )
    oversized = [
        g["goal_id"] for g in goals if len(g.get("tasks") or []) > MAX_TASKS_PER_GOAL
    ]
    checks.append(
        Check(
            "size budget: tasks per goal",
            not oversized,
            f"over budget: {oversized}" if oversized else f"<= {MAX_TASKS_PER_GOAL} each",
            6,
        )
    )

    tasks = [task for goal in goals for task in (goal.get("tasks") or [])]
    without_evidence = [
        task["task_id"]
        for task in tasks
        if task.get("status") == "done" and not task.get("accepted_evidence")
    ]
    checks.append(
        Check(
            "every done task carries accepted evidence",
            bool(tasks) and not without_evidence,
            f"missing: {without_evidence}" if without_evidence else f"{len(tasks)} task(s)",
            2,
        )
    )

    commands = [
        item["exact_command"]
        for task in tasks
        for item in (task.get("accepted_evidence") or [])
    ]
    checks.append(
        Check(
            "evidence names the exact command that ran",
            all(command.strip() for command in commands) and bool(commands),
            "; ".join(commands[:3]) or "no accepted evidence",
            2,
        )
    )

    return checks


@dataclass(frozen=True)
class GitFacts:
    """What the repository says, separated from what it means.

    Collection touches git; judgement is pure over this. That split is what lets
    `backend/tests/integration/test_first_cycle_fixture.py` exercise the verdict
    without a repository, an API or a worker — a checker nobody can test is one
    nobody should trust.
    """

    goal_count: int
    #: (from_ref, into_ref, merge_sha) exactly as the API served them.
    promotions: tuple[tuple[str, str, str], ...]
    #: merge_sha -> git object type, "" when the object is absent.
    object_types: dict[str, str]
    #: merge_sha -> number of parents, 0 when the object is absent.
    parent_counts: dict[str, int]
    default_branch: str
    default_branch_sha: str
    seed_sha: str


def collect_git_facts(repo: Path, evidence: dict[str, Any]) -> GitFacts:
    goals = evidence.get("goals") or []
    promotions = tuple(
        (goal["promotion"]["from_ref"], goal["promotion"]["into_ref"], goal["promotion"]["merge_sha"])
        for goal in goals
        if goal.get("promotion")
    )

    # The refs come from the API. Reconstructing them from the branch naming
    # convention is what this fixture must never do — that reconstruction is the
    # bug migration 0017 exists to retire.
    object_types: dict[str, str] = {}
    parent_counts: dict[str, int] = {}
    for _, _, merge_sha in promotions:
        try:
            object_types[merge_sha] = _git(repo, "cat-file", "-t", merge_sha)
            tokens = _git(repo, "rev-list", "--parents", "-n", "1", merge_sha).split()
            parent_counts[merge_sha] = max(0, len(tokens) - 1)
        except CollectionError:
            object_types[merge_sha] = ""
            parent_counts[merge_sha] = 0

    default_branch = _git(repo, "symbolic-ref", "--short", "HEAD")
    return GitFacts(
        goal_count=len(goals),
        promotions=promotions,
        object_types=object_types,
        parent_counts=parent_counts,
        default_branch=default_branch,
        default_branch_sha=_git(repo, "rev-parse", default_branch),
        seed_sha=_git(repo, "rev-parse", f"{SEED_TAG}^{{commit}}"),
    )


def evaluate_git(facts: GitFacts) -> list[Check]:
    checks: list[Check] = [
        Check(
            "a promotion was recorded for every goal",
            facts.goal_count > 0 and len(facts.promotions) == facts.goal_count,
            f"{len(facts.promotions)} of {facts.goal_count}",
            3,
        )
    ]

    for from_ref, into_ref, merge_sha in facts.promotions:
        kind = facts.object_types.get(merge_sha, "")
        parents = facts.parent_counts.get(merge_sha, 0)
        checks.append(
            Check(
                f"merge {merge_sha[:12]} is a real merge commit",
                kind == "commit" and parents >= 2,
                f"type={kind or 'missing'} parents={parents} ({from_ref} -> {into_ref})",
                3,
            )
        )

    checks.append(
        Check(
            "default branch untouched",
            facts.default_branch_sha == facts.seed_sha,
            f"{facts.default_branch}={facts.default_branch_sha[:12]} "
            f"seed={facts.seed_sha[:12]}",
            4,
        )
    )

    return checks


def evaluate_tier1(repo: Path, evidence: dict[str, Any]) -> list[Check]:
    """The one check a dry-run cannot pass: real code, on the cycle branch."""
    promotions = [g["promotion"] for g in (evidence.get("goals") or []) if g.get("promotion")]
    if not promotions:
        return [Check("implementation written", False, "nothing was promoted", 7)]

    tip = promotions[-1]["merge_sha"]
    try:
        blob = _git(repo, "show", f"{tip}:{SEED_IMPLEMENTATION}")
    except CollectionError as exc:
        return [Check("implementation written", False, str(exc), 7)]
    return [
        Check(
            "implementation written on the promoted tree",
            "NotImplementedError" not in blob,
            f"{SEED_IMPLEMENTATION} @ {tip[:12]}",
            7,
        )
    ]


# ── entry point ──────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--tier", type=int, default=0, choices=(0, 1))
    parser.add_argument("--api", default=os.environ.get("FIRST_CYCLE_API", "http://127.0.0.1:8000"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    token = os.environ.get("PRAXIS_API_TOKEN")
    try:
        plan = _get(args.api, f"/api/plans/{args.plan}", token)
        cycles = plan.get("cycles") or []
        if not cycles:
            raise CollectionError("plan has no cycle — nothing to verify")
        cycle_id = (plan.get("active_cycle") or cycles[-1])["id"]
        evidence = _get(
            args.api, f"/api/plans/{args.plan}/cycles/{cycle_id}/evidence", token
        )
        checks = evaluate_plan(plan, evidence) + evaluate_git(
            collect_git_facts(args.repo, evidence)
        )
        if args.tier == 1:
            checks += evaluate_tier1(args.repo, evidence)
    except CollectionError as exc:
        print(f"collection error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps([asdict(check) for check in checks], indent=2))
    else:
        for check in checks:
            mark = "✓" if check.ok else "✗"
            print(f"  {mark} [{check.expectation}] {check.name} — {check.detail}")

    failed = [check for check in checks if not check.ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
