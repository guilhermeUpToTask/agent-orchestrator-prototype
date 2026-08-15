#!/usr/bin/env python3
"""Verify a happy-path-v1 run against the binary success contract.

``check-success.sh`` answers exactly one question — does ``greet("Ada")`` work on
this checkout. That is expectation 7 of eight, and it is the only one that cannot
pass in Tier 0 (the dry-run runner promotes branches without writing an
implementation). This companion checks the other seven: the lifecycle facts the
API persists, and the Git promotion chain those facts claim to have produced.

    ./scripts/verify_run.py --plan-id "$PLAN_ID"            # Tier 0
    ./scripts/verify_run.py --plan-id "$PLAN_ID" --tier 1   # + pytest on the cycle branch

Exit 0 when every check for the requested tier passes, 1 when any fails, 2 on a
collection error (unreachable API, missing repo) — a failed check and a broken
harness are different findings and must not share an exit code.

Reads only: ``GET /api/plans/{id}`` and ``git``. Nothing here mutates a plan, a
branch, or the database. ``--json`` emits the machine-readable result for a run
directory; the default output is the operator checklist.

The judgement layer (``evaluate_plan`` / ``evaluate_git``) is pure over plain
dicts, so ``backend/tests/integration/test_happy_path_fixture.py`` exercises the
contract without a live API.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SEED_TAG = "happy-path-v1-seed"
SEED_IMPLEMENTATION = "src/happy_path/greeter.py"

# EXPECTATIONS.md size budget. One goal is preferred; three is a reasoner finding
# worth failing on, because pushing through it is how a fixture stops being one.
MAX_GOALS = 2
MAX_TASKS_PER_GOAL = 3


class CollectionError(RuntimeError):
    """The harness could not gather the facts — distinct from a failed check."""


@dataclass(frozen=True)
class Check:
    """One line of the binary success contract."""

    name: str
    ok: bool
    detail: str
    #: Which expectation in EXPECTATIONS.md this line answers, for traceability.
    expectation: int | None = None


@dataclass(frozen=True)
class GitFacts:
    """Everything the Git checks need, collected once so they stay pure."""

    repo_path: str
    default_branch: str
    cycle_branch: str
    cycle_branch_exists: bool
    cycle_head_sha: str | None
    seed_tag_exists: bool
    cycle_descends_from_seed: bool
    default_branch_sha: str | None
    default_branch_diff_vs_seed: list[str] = field(default_factory=list)
    goal_branches: dict[str, bool] = field(default_factory=dict)
    unmerged_goal_branches: list[str] = field(default_factory=list)
    is_isolated_from_orchestrator: bool = True
    orchestrator_repo_path: str | None = None


# --------------------------------------------------------------------------
# judgement — pure functions over plain data
# --------------------------------------------------------------------------


def _tasks(goal: dict[str, Any]) -> list[dict[str, Any]]:
    return list(goal.get("tasks") or [])


def _terminal_cycle(plan: dict[str, Any], cycle_id: str | None = None) -> dict[str, Any] | None:
    """The cycle this run produced.

    Publication completes a cycle and returns the root to IDLE, which clears
    ``active_cycle`` — so a verified run finds its cycle in ``cycles``, not in the
    active slot. Prefer the completed one; fall back to the active cycle so a
    mid-run invocation still reports something useful rather than nothing.

    A project owns ONE long-lived plan, so run *n* is cycle *n* of the same plan
    and ``cycles`` accumulates. Defaulting to the most recent completed cycle is
    right for "verify the run I just did" and wrong for anything else, hence
    ``cycle_id``.
    """
    cycles = list(plan.get("cycles") or [])
    active = plan.get("active_cycle")
    if cycle_id is not None:
        for cycle in [*cycles, *([active] if active else [])]:
            if cycle.get("id") == cycle_id:
                return cycle
        return None
    completed = [c for c in cycles if c.get("status") == "completed"]
    if completed:
        return completed[-1]
    if active:
        return active
    return cycles[-1] if cycles else None


def evaluate_plan(plan: dict[str, Any], cycle_id: str | None = None) -> list[Check]:
    """Expectations 1-6 and the size budget, from persisted plan facts alone."""
    checks: list[Check] = []
    cycle = _terminal_cycle(plan, cycle_id)

    # 1 + 2. There is no gate history on the aggregate — `review_gate` is a single
    # current slot. A cycle EXISTS only via approve-intent then approve-draft, so
    # its presence is the durable proof both gates opened and were approved once.
    if cycle is None:
        detail = (
            f"no cycle {cycle_id!r} on the plan"
            if cycle_id
            else "no cycle on the plan: the intent and/or cycle-draft gate was never approved"
        )
        checks.append(Check("cycle_activated", False, detail, expectation=1))
        return checks
    checks.append(
        Check(
            "cycle_activated",
            True,
            f"cycle {cycle['id']} activated from intent {cycle.get('intent_proposal_id')} "
            f"/ draft {cycle.get('draft_id')} (both gates approved)",
            expectation=1,
        )
    )

    goals = list(cycle.get("goals") or [])
    checks.append(
        Check(
            "goal_count_within_budget",
            0 < len(goals) <= MAX_GOALS,
            f"{len(goals)} goal(s); budget is 1 preferred, {MAX_GOALS} maximum",
            expectation=2,
        )
    )

    # 3. Enrichment ran: the head goal carries a frozen contract and real tasks.
    head = goals[0] if goals else None
    if head is not None:
        enriched = head.get("contract") is not None and bool(_tasks(head))
        checks.append(
            Check(
                "head_goal_enriched",
                enriched,
                f"goal {head['id']} has {'a frozen contract' if head.get('contract') else 'NO contract'}"
                f" and {len(_tasks(head))} task(s)",
                expectation=3,
            )
        )
        oversized = [g["id"] for g in goals if len(_tasks(g)) > MAX_TASKS_PER_GOAL]
        checks.append(
            Check(
                "task_count_within_budget",
                not oversized,
                f"max {MAX_TASKS_PER_GOAL} tasks/goal; over budget: {oversized or 'none'}",
                expectation=2,
            )
        )

    # 4. Every task DONE with accepted, revision-bound evidence. Evidence bound to
    # a stale revision is the failure this check exists for: an edit invalidates
    # it, and accepting it anyway would promote work verified against a contract
    # that no longer holds.
    unfinished = [t["id"] for g in goals for t in _tasks(g) if t.get("status") != "done"]
    checks.append(
        Check(
            "tasks_done",
            not unfinished,
            f"not DONE: {unfinished}" if unfinished else "all tasks DONE",
            expectation=4,
        )
    )

    problems: list[str] = []
    for goal in goals:
        for task in _tasks(goal):
            accepted = [
                e
                for e in (task.get("verification_evidence") or [])
                if e.get("accepted") and e.get("task_revision") == task.get("revision")
            ]
            if not accepted:
                stale = len(task.get("verification_evidence") or [])
                problems.append(
                    f"{task['id']} (revision {task.get('revision')}: "
                    f"{stale} evidence record(s), none accepted at that revision)"
                )
    checks.append(
        Check(
            "evidence_accepted_and_revision_bound",
            not problems,
            "; ".join(problems) if problems else "every task has accepted evidence at its current revision",
            expectation=4,
        )
    )

    incomplete_goals = [g["id"] for g in goals if g.get("status") != "done"]
    checks.append(
        Check(
            "goals_promoted",
            not incomplete_goals,
            f"not DONE: {incomplete_goals}" if incomplete_goals else "all goals DONE",
            expectation=5,
        )
    )

    # 5b. No unresolved block, plan-wide or per-goal (domain unfreeze #14).
    open_blocks: list[str] = []
    plan_block = plan.get("block")
    if plan_block and plan_block.get("active"):
        open_blocks.append(f"plan:{plan_block.get('kind')}")
    for goal_id, block in (plan.get("goal_blocks") or {}).items():
        if block and block.get("active"):
            open_blocks.append(f"{goal_id}:{block.get('kind')}")
    checks.append(
        Check(
            "no_unresolved_block",
            not open_blocks,
            f"open blocks: {open_blocks}" if open_blocks else "no active block",
            expectation=5,
        )
    )

    # 6. Publication recorded — and a non-discard disposition MUST carry the
    # output reference, since that reference is the only durable pointer to where
    # the verified work went.
    disposition = cycle.get("output_disposition")
    reference = cycle.get("output_reference")
    if disposition is None:
        detail = "no publication disposition recorded"
        ok = False
    elif disposition != "discard" and not reference:
        detail = f"disposition {disposition!r} recorded with NO output_reference"
        ok = False
    else:
        detail = f"disposition {disposition!r}" + (f" -> {reference}" if reference else "")
        ok = True
    checks.append(Check("publication_recorded", ok, detail, expectation=6))

    checks.append(
        Check(
            "root_returned_to_idle",
            plan.get("status") == "idle",
            f"root status is {plan.get('status')!r} (expected 'idle' after publication)",
            expectation=6,
        )
    )
    return checks


def evaluate_git(facts: GitFacts) -> list[Check]:
    """Expectation 8 plus the promotion chain the plan facts claim to have built."""
    checks: list[Check] = [
        Check(
            "repository_isolated",
            facts.is_isolated_from_orchestrator,
            f"fixture repo {facts.repo_path}"
            + (
                f" IS the orchestrator repo {facts.orchestrator_repo_path} — the run "
                "would branch the orchestrator's own source"
                if not facts.is_isolated_from_orchestrator
                else " is a separate repository from the orchestrator checkout"
            ),
        ),
        Check(
            "seed_tag_present",
            facts.seed_tag_exists,
            f"{SEED_TAG} {'found' if facts.seed_tag_exists else 'MISSING — re-run materialize.sh'}",
        ),
        Check(
            "cycle_branch_exists",
            facts.cycle_branch_exists,
            f"{facts.cycle_branch}"
            + (f" at {facts.cycle_head_sha}" if facts.cycle_head_sha else " NOT FOUND"),
        ),
    ]

    if facts.cycle_branch_exists:
        checks.append(
            Check(
                "cycle_branch_descends_from_seed",
                facts.cycle_descends_from_seed,
                "cycle branch builds on the seed commit"
                if facts.cycle_descends_from_seed
                else f"cycle branch does not descend from {SEED_TAG} — it branched something else",
            )
        )
        checks.append(
            Check(
                "goal_branches_promoted",
                not facts.unmerged_goal_branches,
                f"unmerged into {facts.cycle_branch}: {facts.unmerged_goal_branches}"
                if facts.unmerged_goal_branches
                else f"all {len(facts.goal_branches)} goal branch(es) merged into the cycle branch",
                expectation=5,
            )
        )

    # 8. The orchestrator must never write the project's default branch. Any diff
    # against the seed tag means plan work escaped the branch hierarchy.
    checks.append(
        Check(
            "default_branch_untouched",
            not facts.default_branch_diff_vs_seed,
            f"{facts.default_branch} differs from {SEED_TAG} in: {facts.default_branch_diff_vs_seed}"
            if facts.default_branch_diff_vs_seed
            else f"{facts.default_branch} still matches {SEED_TAG}",
            expectation=8,
        )
    )
    return checks


# --------------------------------------------------------------------------
# collection — HTTP and git
# --------------------------------------------------------------------------


def fetch_plan(base_url: str, plan_id: str, token: str | None) -> dict[str, Any]:
    request = urllib.request.Request(f"{base_url.rstrip('/')}/api/plans/{plan_id}")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:  # noqa: PERF203 - one call, one message
        raise CollectionError(f"GET /api/plans/{plan_id} returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise CollectionError(f"cannot reach the API at {base_url}: {exc}") from exc
    if not isinstance(payload, dict):
        raise CollectionError("plan detail response was not an object")
    return payload


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise CollectionError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _git_ok(repo: Path, *args: str) -> bool:
    """Run a git predicate where a non-zero exit is an answer, not a failure."""
    return (
        subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            check=False,
        ).returncode
        == 0
    )


def collect_git_facts(repo: Path, cycle_id: str, goal_ids: list[str]) -> GitFacts:
    if not (repo / ".git").exists():
        raise CollectionError(f"not a git repository: {repo}")

    toplevel = _git(repo, "rev-parse", "--show-toplevel")
    orchestrator_repo: str | None = None
    try:
        orchestrator_repo = _git(Path(__file__).resolve().parent, "rev-parse", "--show-toplevel")
    except CollectionError:
        orchestrator_repo = None

    default_branch = os.environ.get("HAPPY_PATH_DEFAULT_BRANCH", "main")
    cycle_branch = f"cycle/{cycle_id}"
    cycle_exists = _git_ok(repo, "rev-parse", "--verify", "--quiet", cycle_branch)
    seed_exists = _git_ok(repo, "rev-parse", "--verify", "--quiet", SEED_TAG)

    diff_paths: list[str] = []
    default_sha: str | None = None
    if seed_exists and _git_ok(repo, "rev-parse", "--verify", "--quiet", default_branch):
        default_sha = _git(repo, "rev-parse", default_branch)
        raw = _git(repo, "diff", "--name-only", SEED_TAG, default_branch)
        diff_paths = [line for line in raw.splitlines() if line]

    goal_branches: dict[str, bool] = {}
    unmerged: list[str] = []
    for goal_id in goal_ids:
        branch = f"goal/{goal_id}"
        exists = _git_ok(repo, "rev-parse", "--verify", "--quiet", branch)
        goal_branches[branch] = exists
        if not exists:
            # A goal branch is deleted after promotion in some flows; absence is
            # only a finding when the cycle branch does not contain its work, and
            # we cannot tell that without the ref. Report it, do not guess.
            unmerged.append(f"{branch} (branch missing)")
        elif cycle_exists and not _git_ok(repo, "merge-base", "--is-ancestor", branch, cycle_branch):
            unmerged.append(branch)

    descends = bool(
        cycle_exists
        and seed_exists
        and _git_ok(repo, "merge-base", "--is-ancestor", SEED_TAG, cycle_branch)
    )

    return GitFacts(
        repo_path=toplevel,
        default_branch=default_branch,
        cycle_branch=cycle_branch,
        cycle_branch_exists=cycle_exists,
        cycle_head_sha=_git(repo, "rev-parse", "--short", cycle_branch) if cycle_exists else None,
        seed_tag_exists=seed_exists,
        cycle_descends_from_seed=descends,
        default_branch_sha=default_sha,
        default_branch_diff_vs_seed=diff_paths,
        goal_branches=goal_branches,
        unmerged_goal_branches=unmerged,
        is_isolated_from_orchestrator=(orchestrator_repo is None or toplevel != orchestrator_repo),
        orchestrator_repo_path=orchestrator_repo,
    )


def run_tier1_check(repo: Path, cycle_branch: str, script: Path) -> Check:
    """Expectation 7: pytest green on a checkout of the promoted cycle branch."""
    worktree = repo.parent / "verify-cycle-checkout"
    try:
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "-f", str(worktree), cycle_branch],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise CollectionError(f"cannot check out {cycle_branch}: {exc.stderr.strip()}") from exc
    try:
        result = subprocess.run(
            [str(script), str(worktree)], capture_output=True, text=True, check=False
        )
        tail = (result.stdout + result.stderr).strip().splitlines()[-3:]
        return Check(
            "implementation_verified",
            result.returncode == 0,
            " / ".join(tail) if tail else f"check-success.sh exit {result.returncode}",
            expectation=7,
        )
    finally:
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "remove", "--force", str(worktree)],
            capture_output=True,
            check=False,
        )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _report(checks: list[Check], tier: int, plan_id: str) -> str:
    lines = [f"happy-path-v1 verification — plan {plan_id}, Tier {tier}", ""]
    for check in checks:
        mark = "PASS" if check.ok else "FAIL"
        suffix = f"  [expectation {check.expectation}]" if check.expectation else ""
        lines.append(f"  [{mark}] {check.name}{suffix}")
        lines.append(f"         {check.detail}")
    failed = [c for c in checks if not c.ok]
    lines.append("")
    lines.append(
        f"{len(checks) - len(failed)}/{len(checks)} checks passed"
        + (f" — FAILED: {', '.join(c.name for c in failed)}" if failed else " — RUN IS GREEN")
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plan-id", required=True)
    parser.add_argument(
        "--cycle-id",
        help="verify this exact cycle; defaults to the most recently completed one "
        "(a project owns one long-lived plan, so cycles accumulate across runs)",
    )
    parser.add_argument(
        "--tier",
        type=int,
        choices=(0, 1),
        default=0,
        help="Tier 1 additionally runs check-success.sh on the cycle branch "
        "(expectation 7, which the dry-run runner cannot satisfy)",
    )
    parser.add_argument("--api", default=os.environ.get("HAPPY_PATH_API", "http://127.0.0.1:8000"))
    parser.add_argument(
        "--repo",
        default=os.environ.get("HAPPY_PATH_REPO")
        or str(Path(os.environ.get("PRAXIS_HOME", Path.home() / ".orchestrator")) / "happy-path-v1" / "repo"),
    )
    parser.add_argument("--json", action="store_true", help="emit the machine-readable result")
    parser.add_argument("--skip-git", action="store_true", help="plan facts only (no repository access)")
    args = parser.parse_args(argv)

    try:
        plan = fetch_plan(args.api, args.plan_id, os.environ.get("PRAXIS_API_TOKEN"))
        checks = evaluate_plan(plan, args.cycle_id)

        cycle = _terminal_cycle(plan, args.cycle_id)
        if cycle is not None and not args.skip_git:
            repo = Path(args.repo).expanduser()
            goal_ids = [g["id"] for g in (cycle.get("goals") or [])]
            facts = collect_git_facts(repo, cycle["id"], goal_ids)
            checks.extend(evaluate_git(facts))
            if args.tier == 1 and facts.cycle_branch_exists:
                checks.append(
                    run_tier1_check(repo, facts.cycle_branch, Path(__file__).parent / "check-success.sh")
                )
    except CollectionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(
            json.dumps(
                {
                    "plan_id": args.plan_id,
                    "tier": args.tier,
                    "green": all(c.ok for c in checks),
                    "checks": [asdict(c) for c in checks],
                },
                indent=2,
            )
        )
    else:
        print(_report(checks, args.tier, args.plan_id))
    return 0 if all(c.ok for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
