"""The happy-path-v1 fixture contract.

This locks the three things a Tier 0/Tier 1 run measures against, so a change to
any of them is a deliberate fixture bump (v2) rather than a silent shift that
makes two runs incomparable:

1. the **seed** — the repository the orchestrator is pointed at must start RED,
   with the exact assertion the brief promises;
2. the **brief** — ``brief.txt`` is posted verbatim to the reasoner, so it must
   stay prose the reasoner can act on and must not drift from the seed's shape;
3. the **checker** — ``verify_run.py``'s judgement over plan and Git facts.

It deliberately does NOT drive the lifecycle: ``test_default_cyclic_execution.py``
already owns that walk. Everything here is pure over plain dicts and files, so it
runs without a database, an API, or a worker.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "happy-path-v1"
SEED = FIXTURE / "seed"


def _load_verifier() -> Any:
    """Import verify_run.py by path — it is fixture tooling, not a package."""
    path = FIXTURE / "scripts" / "verify_run.py"
    spec = importlib.util.spec_from_file_location("happy_path_verify_run", path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    # `@dataclass` resolves annotations through `sys.modules[cls.__module__]`, so
    # the module must be registered before it executes.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


verify_run = _load_verifier()


# --------------------------------------------------------------------------
# 1. the seed
# --------------------------------------------------------------------------


def test_seed_layout_is_locked() -> None:
    for relative in (
        "pyproject.toml",
        "src/happy_path/__init__.py",
        "src/happy_path/greeter.py",
        "tests/test_greeter.py",
    ):
        assert (SEED / relative).is_file(), f"seed is missing {relative}"


def test_seed_starts_red_with_the_exact_promised_assertion() -> None:
    """The whole fixture rests on the seed failing before the run and only after.

    A seed that already passes turns every Tier 1 run green for free — the single
    most expensive way for this fixture to lie.
    """
    greeter = (SEED / "src/happy_path/greeter.py").read_text()
    assert "raise NotImplementedError" in greeter, "seed greeter must start unimplemented"

    test_source = (SEED / "tests/test_greeter.py").read_text()
    assert 'greet("Ada") == "Hello, Ada!"' in test_source, (
        "the seed assertion is quoted verbatim in brief.txt, EXPECTATIONS.md and "
        "check-success.sh; changing it is a fixture v2"
    )


def test_seed_test_fails_against_the_seed_implementation(tmp_path: Path) -> None:
    subprocess.run(["cp", "-a", f"{SEED}/.", str(tmp_path)], check=True)
    result = subprocess.run(
        ["python3", "-m", "pytest", "-q"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(tmp_path / "src"), "PATH": "/usr/bin:/bin:/usr/local/bin"},
        check=False,
    )
    assert result.returncode != 0, f"seed unexpectedly passes:\n{result.stdout}"


# --------------------------------------------------------------------------
# 2. the brief
# --------------------------------------------------------------------------


def test_brief_is_postable_verbatim() -> None:
    """``brief.txt`` exists because ``BRIEF.md``'s prose was being sent to the
    reasoner as part of the brief. Keep it a brief, not a document."""
    brief = (FIXTURE / "brief.txt").read_text()
    assert not brief.lstrip().startswith("#"), "brief.txt must not open with a markdown heading"
    assert 'greet("Ada") == "Hello, Ada!"' in brief, "brief must state the seed's exact assertion"
    assert "python -m pytest -q" in brief, "brief must name the verification command"
    assert "single goal" in brief, "the size budget must reach the architect through the brief"


# --------------------------------------------------------------------------
# 3. the checker's judgement
# --------------------------------------------------------------------------


def _evidence(task_id: str, revision: int, *, accepted: bool = True) -> dict[str, Any]:
    return {
        "id": f"ev-{task_id}-{revision}",
        "task_id": task_id,
        "task_revision": revision,
        "accepted": accepted,
        "exit_code": 0,
    }


def _green_plan() -> dict[str, Any]:
    """The shape a published one-goal run leaves behind."""
    return {
        "id": "plan-1",
        "status": "idle",
        "block": None,
        "goal_blocks": {},
        "active_cycle": None,
        "cycles": [
            {
                "id": "cycle-1",
                "status": "completed",
                "intent_proposal_id": "intent-1",
                "draft_id": "draft-1",
                "output_disposition": "retain_branch",
                "output_reference": "cycle/cycle-1",
                "goals": [
                    {
                        "id": "goal-1",
                        "status": "done",
                        "contract": {"id": "contract-1"},
                        "tasks": [
                            {
                                "id": "task-1",
                                "status": "done",
                                "revision": 1,
                                "verification_evidence": [_evidence("task-1", 1)],
                            }
                        ],
                    }
                ],
            }
        ],
    }


def _failed(checks: list[Any]) -> set[str]:
    return {c.name for c in checks if not c.ok}


def test_published_run_passes_every_plan_check() -> None:
    checks = verify_run.evaluate_plan(_green_plan())
    assert _failed(checks) == set(), [c for c in checks if not c.ok]
    assert {c.name for c in checks} >= {
        "cycle_activated",
        "tasks_done",
        "evidence_accepted_and_revision_bound",
        "goals_promoted",
        "publication_recorded",
        "root_returned_to_idle",
    }


def test_no_cycle_means_no_gate_was_ever_approved() -> None:
    plan = {"id": "plan-1", "status": "waiting", "cycles": [], "active_cycle": None}
    checks = verify_run.evaluate_plan(plan)
    assert _failed(checks) == {"cycle_activated"}


def test_evidence_bound_to_a_stale_revision_is_rejected() -> None:
    """An edit bumps ``task.revision`` and invalidates prior evidence. Accepting
    it anyway would promote work verified against a contract that no longer holds
    — the invariant this checker exists to defend."""
    plan = _green_plan()
    task = plan["cycles"][0]["goals"][0]["tasks"][0]
    task["revision"] = 2  # edited after verification
    checks = verify_run.evaluate_plan(plan)
    assert "evidence_accepted_and_revision_bound" in _failed(checks)


def test_unaccepted_evidence_is_rejected() -> None:
    plan = _green_plan()
    task = plan["cycles"][0]["goals"][0]["tasks"][0]
    task["verification_evidence"] = [_evidence("task-1", 1, accepted=False)]
    checks = verify_run.evaluate_plan(plan)
    assert "evidence_accepted_and_revision_bound" in _failed(checks)


def test_non_discard_disposition_requires_an_output_reference() -> None:
    plan = _green_plan()
    plan["cycles"][0]["output_reference"] = None
    checks = verify_run.evaluate_plan(plan)
    assert "publication_recorded" in _failed(checks)

    plan["cycles"][0]["output_disposition"] = "discard"
    assert "publication_recorded" not in _failed(verify_run.evaluate_plan(plan))


def test_an_active_goal_block_fails_the_run() -> None:
    plan = _green_plan()
    plan["goal_blocks"] = {"goal-1": {"kind": "verification_exhausted", "active": True}}
    checks = verify_run.evaluate_plan(plan)
    assert "no_unresolved_block" in _failed(checks)

    plan["goal_blocks"]["goal-1"]["active"] = False  # resolved blocks are history
    assert "no_unresolved_block" not in _failed(verify_run.evaluate_plan(plan))


def test_goal_fan_out_is_a_finding_not_a_pass() -> None:
    """EXPECTATIONS.md: a draft that invents CI/docs/typing goals is a reasoner
    finding. The checker fails rather than letting an operator push through."""
    plan = _green_plan()
    goals = plan["cycles"][0]["goals"]
    for index in range(2, 5):
        extra = {
            "id": f"goal-{index}",
            "status": "done",
            "contract": {"id": f"contract-{index}"},
            "tasks": [
                {
                    "id": f"task-{index}",
                    "status": "done",
                    "revision": 1,
                    "verification_evidence": [_evidence(f"task-{index}", 1)],
                }
            ],
        }
        goals.append(extra)
    assert "goal_count_within_budget" in _failed(verify_run.evaluate_plan(plan))


def test_a_mid_run_plan_reports_the_active_cycle() -> None:
    """Publication clears ``active_cycle``; before it, ``cycles`` may be empty.
    Both shapes must resolve to the same cycle rather than reporting nothing."""
    plan = _green_plan()
    active = plan["cycles"][0] | {"status": "active", "output_disposition": None}
    mid_run = {**plan, "status": "running", "cycles": [], "active_cycle": active}
    checks = verify_run.evaluate_plan(mid_run)
    assert "cycle_activated" not in _failed(checks)
    assert "publication_recorded" in _failed(checks)


# --- git judgement ---


def _green_git() -> Any:
    return verify_run.GitFacts(
        repo_path="/tmp/happy-path/repo",
        default_branch="main",
        cycle_branch="cycle/cycle-1",
        cycle_branch_exists=True,
        cycle_head_sha="abc1234",
        seed_tag_exists=True,
        cycle_descends_from_seed=True,
        default_branch_sha="def5678",
        default_branch_diff_vs_seed=[],
        goal_branches={"goal/goal-1": True},
        unmerged_goal_branches=[],
        is_isolated_from_orchestrator=True,
        orchestrator_repo_path="/workspaces/agent-orchestrator",
    )


def test_promoted_git_state_passes() -> None:
    assert _failed(verify_run.evaluate_git(_green_git())) == set()


def test_a_written_default_branch_fails() -> None:
    """Expectation 8. The orchestrator branching hierarchy exists precisely so
    the project's default branch is never written by plan work."""
    facts = verify_run.GitFacts(
        **{
            **_green_git().__dict__,
            "default_branch_diff_vs_seed": ["src/happy_path/greeter.py"],
        }
    )
    assert "default_branch_untouched" in _failed(verify_run.evaluate_git(facts))


def test_an_unmerged_goal_branch_fails() -> None:
    facts = verify_run.GitFacts(
        **{**_green_git().__dict__, "unmerged_goal_branches": ["goal/goal-1"]}
    )
    assert "goal_branches_promoted" in _failed(verify_run.evaluate_git(facts))


def test_pointing_the_fixture_at_the_orchestrator_repo_fails() -> None:
    """The repository-binding trap this fixture found on its first live run: a
    project with no ``repo_url`` runs somewhere the checker never looks."""
    facts = verify_run.GitFacts(
        **{
            **_green_git().__dict__,
            "repo_path": "/workspaces/agent-orchestrator",
            "is_isolated_from_orchestrator": False,
        }
    )
    assert "repository_isolated" in _failed(verify_run.evaluate_git(facts))
