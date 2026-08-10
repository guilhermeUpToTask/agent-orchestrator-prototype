"""Un-freeze #17: every field the reasoner AUTHORED becomes editable.

A frozen contract used to be unreachable. `EditRequest`'s eight types touched
task name/description/capabilities/agent/order and goal name/description/deps —
nothing else — and only `Task.semantic_edit` wrote a contract at all, and only
its `revision` and `objective`. Observed live: enrichment froze a `tdd` contract
whose `allowed_scope` named only production files, no agent could satisfy it,
and the only exit was regenerating the whole cycle.

The split that matters is NOT editable-vs-not. It is whether a change alters
what "correct" MEANS:

  * it does  -> bump the revision, invalidate the TestBundle, clear evidence.
    The authored tests no longer describe the task.
  * it does not -> leave the bundle alone. Re-authoring every test to fix a typo
    in a command is what made repair cost a replan in the first place.
"""

from __future__ import annotations

import pytest

from agent_orchestrator.domain.entities.execution_contracts import (
    ContractCriterion,
    TaskContract,
    TestBundle,
    VerificationStrategy,
)
from agent_orchestrator.domain.entities.task import Task
from agent_orchestrator.domain.errors.tasks_errors import InvalidTransitionError
from agent_orchestrator.domain.value_objects.lifecycle import Status

NOW_ISO = "2026-07-26T00:00:00+00:00"


def contract(**overrides) -> TaskContract:
    values = {
        "id": "t1",
        "position": 0,
        "objective": "implement greet",
        "acceptance_criteria": [ContractCriterion(id="t-1", description="greets")],
        "goal_criterion_ids": ["g-1"],
        "allowed_scope": ["src/happy_path/", "tests/"],
        "forbidden_scope": [],
        "verification_commands": ["python -m pytest -q tests/test_greet.py"],
        "verification_strategy": VerificationStrategy.TDD,
        "required_capabilities": ["implementation"],
    }
    values.update(overrides)
    return TaskContract(**values)  # type: ignore[arg-type]


def frozen_task(**contract_overrides) -> Task:
    from datetime import datetime, timezone

    task = Task(id="t1", name="t1", position=0, description="d", contract=contract(**contract_overrides))
    task.test_bundle = TestBundle(
        task_id="t1",
        task_revision=1,
        test_commit_sha="abc",
        protected_file_hashes={"tests/test_greet.py": "hash"},
        criterion_to_tests={"t-1": ["tests/test_greet.py"]},
        verification_strategy=VerificationStrategy.TDD,
        red_or_baseline_evidence_refs=["artifact://red"],
        frozen_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
    )
    return task


# ---------------------------------------------------------------- non-invalidating

def test_fixing_a_verification_command_keeps_the_authored_tests():
    """The exact live repair. Re-authoring the test suite to correct a filename
    is the cost that made a replan the cheaper option."""
    task = frozen_task()

    task.amend_contract(verification_commands=["python -m pytest -q tests/test_greeter.py"])

    assert task.contract.verification_commands == ["python -m pytest -q tests/test_greeter.py"]
    assert task.revision == 1  # what "correct" means did not change
    assert task.test_bundle is not None
    assert task.test_bundle.validates("t1", 1)  # the authored tests survive
    assert task.tdd_stage == "implementation"  # straight back to work


def test_widening_scope_keeps_evidence_because_nothing_accepted_becomes_invalid():
    task = frozen_task(allowed_scope=["src/happy_path/"])
    task.verification_evidence = []

    task.amend_contract(allowed_scope=["src/happy_path/", "tests/", "docs/"])

    assert task.revision == 1
    assert task.test_bundle is not None and task.test_bundle.validates("t1", 1)


def test_narrowing_scope_clears_evidence_because_a_pass_may_no_longer_hold():
    """A candidate accepted under the old scope may have touched a path the new
    one excludes, so its acceptance no longer means anything."""
    task = frozen_task(allowed_scope=["src/", "tests/"])
    task.status = Status.FAILED

    task.amend_contract(allowed_scope=["src/happy_path/", "tests/"])

    assert task.revision == 1  # tests still describe the same behavior
    assert task.test_bundle is not None and task.test_bundle.validates("t1", 1)
    assert task.verification_evidence == []


def test_adding_a_forbidden_path_is_a_narrowing():
    task = frozen_task()

    task.amend_contract(forbidden_scope=["src/happy_path/secrets.py"])

    assert task.contract.forbidden_scope == ["src/happy_path/secrets.py"]
    assert task.verification_evidence == []
    assert task.test_bundle is not None and task.test_bundle.validates("t1", 1)


def test_capabilities_and_goal_criteria_are_bookkeeping_not_meaning():
    task = frozen_task()

    task.amend_contract(required_capabilities=["backend"], goal_criterion_ids=["g-1", "g-2"])

    assert task.contract.required_capabilities == ["backend"]
    assert task.contract.goal_criterion_ids == ["g-1", "g-2"]
    assert task.revision == 1
    assert task.test_bundle is not None and task.test_bundle.validates("t1", 1)


# ---------------------------------------------------------------- invalidating

def test_changing_the_strategy_invalidates_the_authored_tests():
    """TDD vs characterization vs executable_check changes what evidence even
    means, and the bundle records the strategy it was frozen under."""
    task = frozen_task()

    task.semantic_edit(verification_strategy=VerificationStrategy.EXECUTABLE_CHECK)

    assert task.contract.verification_strategy == VerificationStrategy.EXECUTABLE_CHECK
    assert task.revision == 2
    assert task.test_bundle is not None and not task.test_bundle.validates("t1", 2)
    assert task.tdd_stage == "test_authoring"  # re-author


def test_changing_acceptance_criteria_invalidates_the_authored_tests():
    """`freeze_test_bundle` requires criterion ids to match the bundle's map, so
    a stale bundle is structurally invalid the moment criteria move."""
    task = frozen_task()

    task.semantic_edit(
        acceptance_criteria=[
            ContractCriterion(id="t-1", description="greets"),
            ContractCriterion(id="t-2", description="handles empty input"),
        ]
    )

    assert [c.id for c in task.contract.acceptance_criteria] == ["t-1", "t-2"]
    assert task.revision == 2
    assert task.test_bundle is not None and not task.test_bundle.validates("t1", 2)


def test_the_objective_still_follows_the_description():
    task = frozen_task()

    task.semantic_edit(description="implement greet and log it")

    assert task.contract.objective == "implement greet and log it"
    assert task.revision == 2


# ---------------------------------------------------------------- guards

@pytest.mark.parametrize("status", [Status.RUNNING, Status.DONE, Status.SKIPPED])
def test_an_observed_task_is_not_amendable(status: Status):
    """Identity and evidence are the audit trail the finalize re-guard keys off.
    A DONE task's contract has already been merged upward under evidence that
    references it."""
    task = frozen_task()
    task.status = status

    with pytest.raises(InvalidTransitionError):
        task.amend_contract(verification_commands=["pytest -q"])


def test_amend_rejects_a_contract_the_domain_would_not_accept():
    """The whole TaskContract is revalidated, so an edit cannot write a shape
    enrichment itself could never have produced."""
    task = frozen_task()

    with pytest.raises(ValueError):
        task.amend_contract(allowed_scope=[])
    with pytest.raises(ValueError):
        task.amend_contract(verification_commands=[])


def test_amending_a_contractless_task_is_refused():
    task = Task(id="t1", name="t1", position=0, description="d")

    with pytest.raises(ValueError, match="contract"):
        task.amend_contract(verification_commands=["pytest -q"])


# ------------------------------------------------- the goal contract must follow

def test_the_goal_contract_tracks_the_task_it_describes():
    """`planning_handler._enrich_one` builds each Task with `contract=item` where
    `item` is an element of `goal.contract.tasks`, so the two SHARE an object.
    Every contract write does `model_copy`, which rebinds only the task's
    reference — leaving `goal.contract.tasks[i]` describing a contract that no
    longer exists.

    Nothing read it yet, which is why this never bit. But `GoalContract`'s
    coverage invariant validates that list, so leaving it stale means the
    invariant is checking a fiction.
    """
    from datetime import datetime, timezone

    from agent_orchestrator.domain.entities.execution_contracts import GoalContract
    from agent_orchestrator.domain.entities.goal import Goal
    from agent_orchestrator.domain.services.edit_service import resync_goal_contract

    task = frozen_task()
    goal = Goal(
        id="g1",
        name="g1",
        position=0,
        description="",
        tasks=[task],
        contract=GoalContract(
            id="g1",
            objective="ship",
            acceptance_criteria=[ContractCriterion(id="g-1", description="shipped")],
            tasks=[task.contract],  # the shared reference enrichment creates
            frozen_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
        ),
    )

    task.amend_contract(verification_commands=["python -m pytest -q tests/test_greeter.py"])
    assert goal.contract.tasks[0].verification_commands != task.contract.verification_commands

    resync_goal_contract(goal)

    assert goal.contract.tasks[0].verification_commands == ["python -m pytest -q tests/test_greeter.py"]
    assert goal.contract.tasks[0] == task.contract


def test_resync_refuses_an_edit_that_uncovers_a_goal_criterion():
    """`goal_criterion_ids` is editable, so an edit can orphan a goal criterion.
    Re-running the coverage validator is what stops that landing silently."""
    from datetime import datetime, timezone

    import pytest as _pytest
    from pydantic import ValidationError

    from agent_orchestrator.domain.entities.execution_contracts import GoalContract
    from agent_orchestrator.domain.entities.goal import Goal
    from agent_orchestrator.domain.services.edit_service import resync_goal_contract

    task = frozen_task(goal_criterion_ids=["g-1", "g-2"])
    goal = Goal(
        id="g1",
        name="g1",
        position=0,
        description="",
        tasks=[task],
        contract=GoalContract(
            id="g1",
            objective="ship",
            acceptance_criteria=[
                ContractCriterion(id="g-1", description="shipped"),
                ContractCriterion(id="g-2", description="also shipped"),
            ],
            tasks=[task.contract],
            frozen_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
        ),
    )

    task.amend_contract(goal_criterion_ids=["g-1"])  # g-2 now covered by nothing

    with _pytest.raises(ValidationError, match="uncovered goal criteria"):
        resync_goal_contract(goal)


# ------------------------------------------------------------- end to end

def _cyclic_plan_with_contract():
    from datetime import datetime, timezone

    from agent_orchestrator.domain.aggregates.planner_orchestrator import Plan, PlanPhase
    from agent_orchestrator.domain.entities.execution_contracts import GoalContract
    from agent_orchestrator.domain.entities.goal import Goal
    from agent_orchestrator.domain.entities.planning_artifacts import Cycle, CycleStatus, PlanStatus

    task = frozen_task()
    goal = Goal(
        id="g1",
        name="g1",
        position=0,
        description="",
        tasks=[task],
        contract=GoalContract(
            id="g1",
            objective="ship",
            acceptance_criteria=[ContractCriterion(id="g-1", description="shipped")],
            tasks=[task.contract],
            frozen_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
        ),
    )
    return Plan(
        id="p1",
        project_id="project-1",
        brief="b",
        phase=PlanPhase.RUNNING,
        status=PlanStatus.RUNNING,
        paused=True,  # the editable window
        cycles=[
            Cycle(
                id="cycle-1",
                intent_proposal_id="intent-1",
                draft_id="draft-1",
                status=CycleStatus.ACTIVE,
                goals=[goal],
                started_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
            )
        ],
    )


def test_repairing_a_command_needs_no_replan_and_survives_the_round_trip(env_factory):
    """The whole point of un-freeze #17: one wrong string used to cost a replan.

    Runs on both backends because the repaired contract has to SURVIVE
    persistence — the plan is stored as one JSON document, so a field the
    reconstruct path drops would make the edit look applied and then vanish.
    """
    from agent_orchestrator.app.testing.fakes import InMemoryCapabilityRepository
    from agent_orchestrator.app.use_cases.apply_edit import UpdateTaskContract, apply_edit

    env = env_factory()
    env.seed(_cyclic_plan_with_contract())

    apply_edit(
        "p1",
        UpdateTaskContract(
            goal_id="g1",
            task_id="t1",
            verification_commands=["python -m pytest -q tests/test_greeter.py"],
        ),
        env.uow,
        InMemoryCapabilityRepository(),
        env.agents,
    )

    stored = env.stored("p1")
    task = stored.active_cycle.goals[0].tasks[0]
    assert task.contract.verification_commands == ["python -m pytest -q tests/test_greeter.py"]
    assert task.revision == 1  # the authored tests were not thrown away
    assert task.test_bundle is not None and task.test_bundle.validates("t1", 1)
    # and the goal contract followed it through persistence
    assert stored.active_cycle.goals[0].contract.tasks[0].verification_commands == [
        "python -m pytest -q tests/test_greeter.py"
    ]


def test_a_scope_repair_is_persisted_and_keeps_the_bundle(env_factory):
    from agent_orchestrator.app.testing.fakes import InMemoryCapabilityRepository
    from agent_orchestrator.app.use_cases.apply_edit import UpdateTaskContract, apply_edit

    env = env_factory()
    env.seed(_cyclic_plan_with_contract())

    apply_edit(
        "p1",
        UpdateTaskContract(
            goal_id="g1", task_id="t1", allowed_scope=["src/happy_path/", "tests/", "docs/"]
        ),
        env.uow,
        InMemoryCapabilityRepository(),
        env.agents,
    )

    task = env.stored("p1").active_cycle.goals[0].tasks[0]
    assert task.contract.allowed_scope == ["src/happy_path/", "tests/", "docs/"]
    assert task.test_bundle is not None and task.test_bundle.validates("t1", 1)


def test_a_plan_written_before_the_unfreeze_still_rehydrates():
    """Plans persist as one JSON document, so every additive field must tolerate
    its own absence. A pre-#17 cycle simply has no retained intent."""
    plan = _cyclic_plan_with_contract()
    payload = plan.model_dump(mode="json")
    for cycle in payload["cycles"]:
        cycle.pop("approved_intent", None)  # exactly what an older row looks like

    from agent_orchestrator.domain.aggregates.planner_orchestrator import Plan

    restored = Plan.model_validate(payload)

    assert restored.cycles[0].approved_intent is None
    assert restored.active_cycle.goals[0].tasks[0].contract is not None


def test_a_broken_contract_blocks_then_the_repair_lets_execution_finish(env_factory):
    """The full operator story, driven through the real handlers on both backends.

    Deliberately NOT a live fixture. There is no control point between the two
    stages: the worker enriches and executes under ONE claim, and the pause gate
    blocks CLAIMS, so nothing can hold a plan at the contract boundary from
    outside. Catching a contract mid-flight would mean stopping the worker, which
    is outside an API-only walkthrough — so the boundary is exercised here, where
    the handlers can be stepped one at a time.
    """
    import asyncio

    from agent_orchestrator.app.handlers.base import Signal
    from agent_orchestrator.app.handlers.execution_handler import ExecutionHandler
    from agent_orchestrator.app.testing.fakes import InMemoryCapabilityRepository
    from agent_orchestrator.app.use_cases.apply_edit import UpdateTaskContract, apply_edit
    from agent_orchestrator.domain.entities.planning_artifacts import PlanStatus

    env = env_factory()
    env.seed(_cyclic_plan_with_contract())

    goal_id, task_id = "g1", "t1"
    original = env.stored("p1").active_cycle.goals[0].tasks[0].contract.verification_commands

    # break it: a command no candidate can satisfy
    apply_edit(
        "p1",
        UpdateTaskContract(
            goal_id=goal_id,
            task_id=task_id,
            verification_commands=["test -f THIS_FILE_DOES_NOT_EXIST"],
        ),
        env.uow,
        InMemoryCapabilityRepository(),
        env.agents,
    )
    broken = env.stored("p1")
    broken.paused = False
    broken.bump_version()  # CAS: a save without a bump is a conflict, by design
    with env.uow:
        env.uow.plans.save(broken)

    handler = ExecutionHandler(*env.args[1:])  # runner, agents, ws, sink, clock
    for _ in range(6):  # ceiling is 2 verification attempts, plus backoff ticks
        with env.uow:
            loaded = env.uow.plans.get("p1")
        if loaded.goal_blocks.get(goal_id) is not None:
            break
        asyncio.run(handler.handle("p1", loaded, env.uow))
        env.clock.advance(3600)

    blocked = env.stored("p1")
    block = blocked.goal_blocks.get(goal_id)
    assert block is not None and block.active
    assert "edit_task" in block.legal_resolutions  # the repair must be reachable

    # repair it, inside the recovery window the block opens
    apply_edit(
        "p1",
        UpdateTaskContract(goal_id=goal_id, task_id=task_id, verification_commands=original),
        env.uow,
        InMemoryCapabilityRepository(),
        env.agents,
    )
    repaired = env.stored("p1")
    assert repaired.active_cycle.goals[0].tasks[0].contract.verification_commands == original
    assert repaired.active_cycle.goals[0].tasks[0].revision == 1  # tests survived
    assert len(repaired.cycles) == 1  # a repair, not a replan
    assert repaired.status != PlanStatus.IDLE
    assert Signal  # the handler contract is what drove this


# --------------------------------------------------- automatic repair (phase 5)

class _TrackedPaths:
    """A `RepositoryReader` stub — repair only needs the path list."""

    def __init__(self, paths):
        self.paths = paths

    def list_paths(self, project_id, *, prefix="", max_entries=200):
        return list(self.paths)

    def orientation(self, project_id):  # pragma: no cover - unused by repair
        raise AssertionError("unused")

    def read_file(self, project_id, path, *, max_bytes=20_000):  # pragma: no cover
        raise AssertionError("unused")

    def search(self, project_id, pattern, *, path_prefix="", max_hits=50):  # pragma: no cover
        raise AssertionError("unused")

    def exists(self, project_id, path):  # pragma: no cover
        raise AssertionError("unused")


class _RejectsTheCandidate:
    """Fails the way the real finalize does when the CONTRACT is the problem.

    The reason string is what `_repair_contract` classifies on, and it is exactly
    what `_finalize_test_author` emits when a tdd contract leaves the test author
    nowhere legal to write.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def run(self, task, spec, *, idempotency_key, event_sink, workspace):
        from agent_orchestrator.app.ports import TaskFailed
        from agent_orchestrator.domain.value_objects.lifecycle import FailureKind

        self.calls += 1
        raise TaskFailed(
            "test author modified production paths: ['src/happy_path/greeter.py']",
            FailureKind.VERIFICATION_ERROR,
        )


def _repairing_handler(env, artifacts, paths, runner=None):
    from agent_orchestrator.app.handlers.execution_handler import ExecutionHandler

    _, agents, ws, sink, clock = env.args[1:6]
    return ExecutionHandler(
        runner or _RejectsTheCandidate(),
        agents,
        ws,
        sink,
        clock,
        repository_reader=_TrackedPaths(paths),
        planning_artifacts=artifacts,
    )


def test_an_unsatisfiable_contract_is_repaired_instead_of_blocking(env_factory):
    """The live failure, automated. A `tdd` contract whose scope named only
    production files left the test author nowhere legal to write; both attempts
    died and a human was required. The scope is now widened with the repository's
    own test directory and the task requeued — no block, no replan."""
    import asyncio

    from agent_orchestrator.app.testing.fakes import InMemoryPlanningArtifactStore

    env = env_factory()
    plan = _cyclic_plan_with_contract()
    plan.paused = False
    # the shape that cannot be satisfied: tdd, no test path in scope
    plan.cycles[0].goals[0].tasks[0].contract = contract(allowed_scope=["src/happy_path/"])
    env.seed(plan)

    artifacts = InMemoryPlanningArtifactStore()
    handler = _repairing_handler(
        env, artifacts, ["src/happy_path/greeter.py", "tests/test_greeter.py"]
    )

    for _ in range(4):
        with env.uow:
            loaded = env.uow.plans.get("p1")
        if loaded.goal_blocks.get("g1") is not None:
            break
        if "tests/" in loaded.active_cycle.goals[0].tasks[0].contract.allowed_scope:
            break
        asyncio.run(handler.handle("p1", loaded, env.uow))
        env.clock.advance(3600)

    stored = env.stored("p1")
    assert stored.goal_blocks.get("g1") is None  # no human was asked
    assert "tests/" in stored.active_cycle.goals[0].tasks[0].contract.allowed_scope
    assert stored.active_cycle.goals[0].tasks[0].revision == 1  # tests not re-authored

    (record,) = artifacts.latest("p1", "contract_repair", goal_id="g1")
    assert record.outcome == "committed"
    assert "allowed_scope" in record.payload["repair"]


def test_repair_is_bounded_and_still_ends_in_a_block(env_factory):
    """A system that never blocks is a system that burns budget invisibly. Past
    the bound the backstop opens exactly as before."""
    import asyncio
    from datetime import datetime, timezone

    from agent_orchestrator.app.ports import PlanningArtifact
    from agent_orchestrator.app.testing.fakes import InMemoryPlanningArtifactStore

    env = env_factory()
    plan = _cyclic_plan_with_contract()
    plan.paused = False
    plan.cycles[0].goals[0].tasks[0].contract = contract(allowed_scope=["src/happy_path/"])
    env.seed(plan)

    artifacts = InMemoryPlanningArtifactStore()
    for _ in range(2):  # the bound is already spent
        artifacts.append(
            PlanningArtifact(
                plan_id="p1",
                goal_id="g1",
                purpose="contract_repair",
                sequence=0,
                input_fingerprint="t1:1",
                outcome="committed",
                payload={"task_id": "t1", "repair": "earlier"},
                created_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
            )
        )
    handler = _repairing_handler(
        env, artifacts, ["src/happy_path/greeter.py", "tests/test_greeter.py"]
    )

    for _ in range(6):
        with env.uow:
            loaded = env.uow.plans.get("p1")
        if loaded.goal_blocks.get("g1") is not None:
            break
        asyncio.run(handler.handle("p1", loaded, env.uow))
        env.clock.advance(3600)

    blocked = env.stored("p1")
    assert blocked.goal_blocks.get("g1") is not None  # the backstop still opens
    assert len(artifacts.latest("p1", "contract_repair", goal_id="g1", limit=10)) == 2


def test_without_repository_sight_the_behaviour_is_exactly_as_before(env_factory):
    """Repair is derived from repository facts. With none, it never fires — and
    must not change the outcome for anyone who has not wired it."""
    import asyncio

    from agent_orchestrator.app.handlers.execution_handler import ExecutionHandler

    env = env_factory()
    plan = _cyclic_plan_with_contract()
    plan.paused = False
    plan.cycles[0].goals[0].tasks[0].contract = contract(allowed_scope=["src/happy_path/"])
    env.seed(plan)

    handler = ExecutionHandler(*env.args[1:6])  # no reader, no artifact store  # noqa: F841
    handler = ExecutionHandler(
        _RejectsTheCandidate(), *env.args[2:6]
    )  # same failure, no repair wiring
    for _ in range(6):
        with env.uow:
            loaded = env.uow.plans.get("p1")
        if loaded.goal_blocks.get("g1") is not None:
            break
        asyncio.run(handler.handle("p1", loaded, env.uow))
        env.clock.advance(3600)

    assert env.stored("p1").goal_blocks.get("g1") is not None


# ------------------------------------------ transient promotion retry (phase 5)

class _FlakyMerge:
    """A workspace whose goal merge fails environmentally, then succeeds."""

    def __init__(self, failures: int, message: str) -> None:
        self.remaining = failures
        self.message = message
        self.merges = 0

    async def merge_goal(self, plan_id, cycle_id, goal_id):
        self.merges += 1
        if self.remaining > 0:
            self.remaining -= 1
            raise RuntimeError(self.message)
        return "merged-sha"

    def __getattr__(self, item):  # delegate everything else to the real fake
        return getattr(self._inner, item)


def _promotion_env(env, workspace, artifacts):
    """Seed the promotion reservation the real path holds when merge_goal runs.

    Both code paths re-check it — an unmatched reservation means someone else
    owns the goal, and neither retry nor block may touch their state.
    """
    from agent_orchestrator.app.handlers.execution_handler import ExecutionHandler

    reserved = env.stored("p1")
    reserved.reserve_promotion("g1", "res-1")
    reserved.bump_version()
    with env.uow:
        env.uow.plans.save(reserved)

    runner, agents, _ws, sink, clock = env.args[1:6]
    return ExecutionHandler(
        runner, agents, workspace, sink, clock, planning_artifacts=artifacts
    )


def test_a_transient_merge_failure_releases_and_retries_instead_of_blocking(env_factory):
    """Observed risk, not observed failure: the block opened on the FIRST
    exception from merge_goal, so a stale worktree registration or a held index
    lock threw away a fully verified goal and asked a human to replan."""
    import asyncio

    from agent_orchestrator.app.handlers.base import Signal
    from agent_orchestrator.app.testing.fakes import InMemoryPlanningArtifactStore

    env = env_factory()
    env.seed(_cyclic_plan_with_contract())
    artifacts = InMemoryPlanningArtifactStore()
    workspace = _FlakyMerge(1, "fatal: 'cycle-merge-x' is already checked out at '/tmp/x'")
    handler = _promotion_env(env, workspace, artifacts)

    signal = asyncio.run(
        handler._promoter.promote("p1", ("res-1", "cycle-1", "g1"), env.uow)
    )

    assert signal == Signal.NOT_READY  # worker sleeps and re-attempts
    stored = env.stored("p1")
    assert stored.goal_blocks.get("g1") is None  # no human asked
    # the reservation MUST be released, or the retry it exists to allow is blocked
    assert stored.goal_promotion_reservations.get("g1") is None
    assert len(artifacts.latest("p1", "goal_promotion_retry", goal_id="g1")) == 1


def test_a_merge_conflict_still_blocks_immediately(env_factory):
    """Two goals genuinely changed the same lines. No retry resolves that, and
    burning worktrees pretending otherwise helps nobody."""
    import asyncio

    from agent_orchestrator.app.testing.fakes import InMemoryPlanningArtifactStore

    env = env_factory()
    env.seed(_cyclic_plan_with_contract())
    artifacts = InMemoryPlanningArtifactStore()
    workspace = _FlakyMerge(1, "CONFLICT (content): Merge conflict in src/happy_path/greeter.py")
    handler = _promotion_env(env, workspace, artifacts)

    asyncio.run(handler._promoter.promote("p1", ("res-1", "cycle-1", "g1"), env.uow))

    blocked = env.stored("p1")
    assert blocked.goal_blocks.get("g1") is not None
    assert blocked.goal_blocks["g1"].kind == "goal_promotion_failure"
    assert artifacts.latest("p1", "goal_promotion_retry", goal_id="g1") == []


def test_promotion_retries_are_bounded(env_factory):
    """If two re-attempts do not clear it, the condition is not momentary."""
    import asyncio
    from datetime import datetime, timezone

    from agent_orchestrator.app.ports import PlanningArtifact
    from agent_orchestrator.app.testing.fakes import InMemoryPlanningArtifactStore

    env = env_factory()
    env.seed(_cyclic_plan_with_contract())
    artifacts = InMemoryPlanningArtifactStore()
    for _ in range(2):
        artifacts.append(
            PlanningArtifact(
                plan_id="p1",
                goal_id="g1",
                purpose="goal_promotion_retry",
                sequence=0,
                input_fingerprint="g1",
                outcome="abandoned",
                created_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
            )
        )
    workspace = _FlakyMerge(5, "fatal: Unable to create '/repo/.git/index.lock': File exists.")
    handler = _promotion_env(env, workspace, artifacts)

    asyncio.run(handler._promoter.promote("p1", ("res-1", "cycle-1", "g1"), env.uow))

    assert env.stored("p1").goal_blocks.get("g1") is not None  # the backstop holds
