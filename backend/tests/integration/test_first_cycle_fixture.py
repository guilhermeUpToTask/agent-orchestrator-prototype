"""The first-cycle-v1 fixture contract.

`first-cycle-v1` is the walkthrough an operator runs FIRST, so its judgement has
to be trustworthy before anyone trusts a run through it. This locks the parts a
live run cannot check on itself:

1. the **seed** starts RED — `slugify` raises, `tests/` is empty, so a run that
   reports success without the agent writing anything is impossible;
2. the **brief** stays prose the reasoner can act on, and keeps naming the file
   the seed actually ships;
3. the **checker** — `verify_run.py`'s judgement over plan, evidence and git
   facts, including that it refuses a run the API would call finished.

It deliberately does NOT drive the lifecycle: `test_default_cyclic_execution.py`
owns that walk. Everything here is pure over plain dicts and files, so it runs
without a database, an API, or a worker.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "first-cycle-v1"
SEED = FIXTURE / "seed"


def _load_verifier() -> Any:
    """Import verify_run.py by path — it is fixture tooling, not a package."""
    path = FIXTURE / "scripts" / "verify_run.py"
    spec = importlib.util.spec_from_file_location("first_cycle_verify_run", path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


verifier = _load_verifier()


# ── 1. the seed starts red ───────────────────────────────────────────────────


def test_the_seed_implementation_is_unimplemented() -> None:
    source = (SEED / "src" / "first_cycle" / "slug.py").read_text()

    assert "NotImplementedError" in source
    assert "def slugify" in source


def test_the_seed_ships_no_test_for_the_agent_to_lean_on() -> None:
    """The brief asks for the test FIRST. A seeded test would let a run pass
    without the agent authoring anything."""
    tests = sorted(p.name for p in (SEED / "tests").iterdir())

    assert tests == [".gitkeep"], f"seed tests/ is not empty: {tests}"


# ── 2. the brief matches the seed ────────────────────────────────────────────


def test_the_brief_names_the_file_the_seed_actually_ships() -> None:
    brief = (FIXTURE / "brief.txt").read_text()

    assert "src/first_cycle/slug.py" in brief
    assert (SEED / "src" / "first_cycle" / "slug.py").exists()


def test_the_brief_asks_for_one_goal_and_the_smallest_task_set() -> None:
    """The size budget in EXPECTATIONS.md is only enforceable if the brief
    actually asked for it."""
    brief = (FIXTURE / "brief.txt").read_text().lower()

    assert "single goal" in brief
    assert "smallest possible task set" in brief


# ── 3. the checker's judgement ───────────────────────────────────────────────


def _plan(**overrides: Any) -> dict[str, Any]:
    return {"status": "idle", "activity": "idle", "block": None, "goal_blocks": {}, **overrides}


def _evidence(**overrides: Any) -> dict[str, Any]:
    return {
        "disposition": {"disposition": "retain_branch", "output_reference": "cycle/c1"},
        "goals": [
            {
                "goal_id": "g1",
                "status": "done",
                "promotion": {
                    "from_ref": "goal/g1",
                    "into_ref": "cycle/c1",
                    "merge_sha": "a" * 40,
                },
                "tasks": [
                    {
                        "task_id": "t1",
                        "status": "done",
                        "accepted_evidence": [
                            {"exact_command": "python -m pytest -q tests/test_slug.py"}
                        ],
                    }
                ],
            }
        ],
        **overrides,
    }


def _named(checks: list[Any], name: str) -> Any:
    return next(check for check in checks if check.name == name)


def test_a_clean_run_passes_every_plan_check() -> None:
    checks = verifier.evaluate_plan(_plan(), _evidence())

    assert [check.name for check in checks if not check.ok] == []


def test_a_plan_still_running_is_not_a_finished_run() -> None:
    checks = verifier.evaluate_plan(_plan(status="running"), _evidence())

    assert not _named(checks, "plan settled idle").ok


def test_an_outstanding_goal_block_fails_the_run() -> None:
    """Un-freeze #14: a per-goal block leaves the root RUNNING, so checking the
    plan-wide scalar alone would call a blocked run successful."""
    plan = _plan(goal_blocks={"g1": {"explanation": "contract unsatisfiable"}})

    checks = verifier.evaluate_plan(plan, _evidence())

    assert not _named(checks, "no block outstanding").ok


def test_a_done_task_without_accepted_evidence_fails_the_run() -> None:
    evidence = _evidence()
    evidence["goals"][0]["tasks"][0]["accepted_evidence"] = []

    checks = verifier.evaluate_plan(_plan(), evidence)

    assert not _named(checks, "every done task carries accepted evidence").ok


def test_a_missing_disposition_fails_the_run() -> None:
    checks = verifier.evaluate_plan(_plan(), _evidence(disposition=None))

    assert not _named(checks, "output disposition recorded").ok


def test_the_size_budget_refuses_a_platform_rewrite() -> None:
    """A reasoner answering this brief with many goals has misread it; pushing
    the run through anyway is how a fixture stops being one."""
    evidence = _evidence()
    evidence["goals"] = [dict(evidence["goals"][0], goal_id=f"g{i}") for i in range(4)]

    checks = verifier.evaluate_plan(_plan(), evidence)

    assert not _named(checks, "size budget: goals").ok


def test_the_size_budget_refuses_an_oversized_goal() -> None:
    evidence = _evidence()
    task = evidence["goals"][0]["tasks"][0]
    evidence["goals"][0]["tasks"] = [dict(task, task_id=f"t{i}") for i in range(5)]

    checks = verifier.evaluate_plan(_plan(), evidence)

    assert not _named(checks, "size budget: tasks per goal").ok


def _git_facts(**overrides: Any) -> Any:
    sha = "a" * 40
    return verifier.GitFacts(
        **{
            "goal_count": 1,
            "promotions": (("goal/g1", "cycle/c1", sha),),
            "object_types": {sha: "commit"},
            "parent_counts": {sha: 2},
            "default_branch": "main",
            "default_branch_sha": "b" * 40,
            "seed_sha": "b" * 40,
            **overrides,
        }
    )


def test_a_promoted_run_passes_every_git_check() -> None:
    assert [check.name for check in verifier.evaluate_git(_git_facts()) if not check.ok] == []


def test_a_goal_with_no_promotion_fails() -> None:
    """Serving a merge SHA is the claim; this is the check that it was made."""
    checks = verifier.evaluate_git(_git_facts(promotions=(), object_types={}, parent_counts={}))

    assert not _named(checks, "a promotion was recorded for every goal").ok


def test_a_promotion_naming_a_sha_the_repository_does_not_have_fails() -> None:
    """The evidence endpoint could serve a SHA that was never written; the
    fixture's job is to disbelieve it until git agrees."""
    sha = "a" * 40
    checks = verifier.evaluate_git(_git_facts(object_types={sha: ""}, parent_counts={sha: 0}))

    assert not _named(checks, f"merge {sha[:12]} is a real merge commit").ok


def test_a_fast_forward_is_not_a_promotion() -> None:
    """Promotion is `--no-ff` by design: a single-parent commit means the goal
    branch was not merged, it was replayed."""
    sha = "a" * 40
    checks = verifier.evaluate_git(_git_facts(parent_counts={sha: 1}))

    assert not _named(checks, f"merge {sha[:12]} is a real merge commit").ok


def test_a_written_default_branch_fails() -> None:
    """The branch ladder exists precisely so plan work never writes the
    project's default branch."""
    checks = verifier.evaluate_git(_git_facts(default_branch_sha="c" * 40))

    assert not _named(checks, "default branch untouched").ok
