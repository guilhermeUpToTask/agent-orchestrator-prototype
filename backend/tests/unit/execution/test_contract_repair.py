"""Repairing a contract no agent could satisfy, instead of asking a human.

Both classes here were produced by a real reasoner in a live Tier 1 run:

  * `verification_commands: ["pytest -q tests/test_greet.py"]` against a
    repository whose file is `tests/test_greeter.py` — every attempt failed on a
    command that could never pass.
  * `verification_strategy: tdd` with `allowed_scope: ["src/happy_path/greeter.py"]`
    — the TDD stage authors the failing test and may not touch production files,
    so the test author had nowhere legal to write. Both attempts died
    `test author modified production paths` and the goal blocked.

Phase 1 now rejects both at SUBMISSION time, so a fresh contract cannot be frozen
this way. This is for the ones that already are — and for repository drift, where
a contract that was satisfiable when frozen stops being so.

Deliberately DETERMINISTIC, not a second reasoner session. Both repairs are the
exact move an operator would make, both are derived from facts already in the
repository, and neither needs a domain un-freeze or a provider call. A repair
nobody can predict is not a repair, it is another guess.
"""

from __future__ import annotations

import pytest

from agent_orchestrator.app.contract_repair import propose_repair
from agent_orchestrator.domain.entities.execution_contracts import (
    ContractCriterion,
    TaskContract,
    VerificationStrategy,
)

TRACKED = [
    "pyproject.toml",
    "src/happy_path/__init__.py",
    "src/happy_path/greeter.py",
    "tests/test_greeter.py",
]


def contract(**overrides) -> TaskContract:
    values = {
        "id": "t1",
        "position": 0,
        "objective": "implement greet",
        "acceptance_criteria": [ContractCriterion(id="t-1", description="greets")],
        "goal_criterion_ids": ["g-1"],
        "allowed_scope": ["src/happy_path/", "tests/"],
        "verification_commands": ["python -m pytest -q tests/test_greeter.py"],
        "verification_strategy": VerificationStrategy.TDD,
    }
    values.update(overrides)
    return TaskContract(**values)  # type: ignore[arg-type]


def test_a_command_naming_a_near_twin_is_rewritten_to_the_real_path():
    broken = contract(verification_commands=["python -m pytest -q tests/test_greet.py"])

    repair = propose_repair(broken, ["authoritative verification command failed"], TRACKED)

    assert repair is not None
    assert repair.verification_commands == ["python -m pytest -q tests/test_greeter.py"]
    assert "tests/test_greeter.py" in repair.description


def test_a_tdd_contract_with_no_test_path_gains_the_repositorys_test_directory():
    broken = contract(allowed_scope=["src/happy_path/greeter.py"])

    repair = propose_repair(
        broken, ["test author modified production paths: ['src/happy_path/greeter.py']"], TRACKED
    )

    assert repair is not None
    assert repair.allowed_scope == ["src/happy_path/greeter.py", "tests/"]
    # widening only: nothing previously in scope is removed, so accepted evidence
    # stays valid and the authored tests are never re-authored
    assert set(repair.allowed_scope) > set(broken.allowed_scope)


def test_a_satisfiable_contract_is_left_alone():
    """The agent's work was wrong, not the contract. Rewriting a good contract to
    make a bad candidate pass is the one thing this must never do."""
    assert propose_repair(contract(), ["path outside allowed scope: docs/x.md"], TRACKED) is None


def test_no_repository_sight_means_no_repair():
    """Every repair here is derived from repository facts. Without them there is
    nothing to derive, and guessing is what caused the failure in the first place."""
    assert propose_repair(contract(verification_commands=["pytest tests/nope.py"]), [], []) is None


def test_a_command_naming_something_with_no_near_twin_is_not_invented():
    """A task may legitimately create a file that does not exist yet. Only a
    missing path WITH a close match is a typo."""
    broken = contract(verification_commands=["python -m pytest -q tests/test_formatting.py"])

    assert propose_repair(broken, ["authoritative verification command failed"], TRACKED) is None


def test_an_executable_check_contract_also_gets_somewhere_to_author():
    """`executable_check` used to be exempt from this repair, on the assumption
    that it has no authoring stage. It does — whenever the contract names no
    check that already exists — so it needs somewhere legal to write exactly
    like the others."""
    broken = contract(
        allowed_scope=["src/happy_path/greeter.py"],
        verification_strategy=VerificationStrategy.EXECUTABLE_CHECK,
    )

    repair = propose_repair(broken, ["test author produced no executable checks"], TRACKED)

    assert repair is not None
    assert repair.allowed_scope == ["src/happy_path/greeter.py", "tests/"]


def test_a_repository_with_no_tests_cannot_supply_a_test_path():
    broken = contract(allowed_scope=["src/happy_path/greeter.py"])

    assert propose_repair(broken, ["test author modified production paths"], ["agent_orchestrator/app.py"]) is None


@pytest.mark.parametrize(
    "reasons",
    [
        ["goal promotion targets a superseded cycle"],
        ["rate limited by provider"],
        [],
    ],
)
def test_failures_that_say_nothing_about_the_contract_produce_no_repair(reasons):
    broken = contract(verification_commands=["python -m pytest -q tests/test_greet.py"])

    assert propose_repair(broken, reasons, TRACKED) is None


def test_queued_artifacts_flush_after_the_transaction_and_tolerate_a_failing_store():
    """The repair record is QUEUED during the finalize transaction and written
    once it closes.

    Writing it inline self-deadlocks on real SQLite: the store keeps its own
    short transaction so the record survives a plan rollback, which means a
    second connection, and SQLite/WAL allows one writer. The plan connection
    holds the lock while synchronously waiting for the artifact connection.
    Observed live in a Tier 1 run — five attempts died `Database stayed locked
    beyond retry budget`, each losing the repair, so the identical repair was
    recomputed until the retry budget ran out and the goal blocked.

    The in-memory fakes cannot reproduce that (one process, no lock), so this
    locks the two properties the fix owns: the queue drains, and a store that
    raises never fails the run.
    """
    from datetime import datetime, timezone

    from agent_orchestrator.app.handlers.execution_handler import ExecutionHandler
    from agent_orchestrator.app.ports import PlanningArtifact
    from agent_orchestrator.app.testing.fakes import (
        CollectingEventSink,
        DummyAgentRunner,
        FakeClock,
        InMemoryAgentRepository,
        InMemoryPlanningArtifactStore,
        NoOpWorkspace,
    )

    def artifact(purpose: str) -> PlanningArtifact:
        return PlanningArtifact(
            plan_id="p1",
            goal_id="g1",
            purpose=purpose,
            sequence=0,
            input_fingerprint="t1:1",
            outcome="committed",
            payload={"repair": "x"},
            rejection_reasons=(),
            created_at=datetime(2026, 7, 27, tzinfo=timezone.utc),
        )

    store = InMemoryPlanningArtifactStore()
    handler = ExecutionHandler(
        DummyAgentRunner({}),
        InMemoryAgentRepository([], None),
        NoOpWorkspace(),
        CollectingEventSink(),
        FakeClock(),
        planning_artifacts=store,
    )

    handler._pending_artifacts.append(artifact("contract_repair"))
    assert store.latest("p1", "contract_repair", goal_id="g1") == []  # not yet written
    handler._flush_pending_artifacts()
    assert len(store.latest("p1", "contract_repair", goal_id="g1")) == 1
    assert handler._pending_artifacts == []  # drained

    class Raising:
        def append(self, _artifact):
            raise RuntimeError("store is down")

    handler._planning_artifacts = Raising()  # type: ignore[assignment]
    handler._pending_artifacts.append(artifact("contract_repair"))
    handler._flush_pending_artifacts()  # best-effort: must not raise
    assert handler._pending_artifacts == []  # drained even though the write failed
