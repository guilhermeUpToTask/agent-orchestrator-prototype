from __future__ import annotations

import asyncio

import pytest
from click.testing import CliRunner
from cryptography.fernet import Fernet

from src.app.execution_records import RuntimeCircuit
from src.app.handlers.planning_handler import PlanningHandler
from src.app.use_cases.claim_ready_goal import claim_ready_goal
from src.app.use_cases.cyclic_planning import activate_cycle, approve_intent, propose_intent
from src.app.use_cases.run_worker import drive_goal, drive_plan
from src.domain.aggregates.planner_orchestrator import Plan
from src.domain.entities.planning_artifacts import PlanStatus, ProposalKind
from src.domain.entities.project_definition import ProjectDefinition
from src.domain.value_objects.lifecycle import Status
from src.infra.cli.main import cli
from src.infra.container import AppContainer
from src.infra.db.tables import Base
from tests.integration.cyclic_walk import _git, _init_trunk_repo, drive_cycle_to_publication

pytestmark = pytest.mark.integration


def test_shipped_stub_and_dry_run_execute_a_cycle_to_publication_gate(
    tmp_path,
    monkeypatch,
) -> None:
    walk = drive_cycle_to_publication(tmp_path, monkeypatch)

    # G9: "where did the code go" must be answerable without reconstructing a
    # branch name from a convention the cyclic ladder does not follow.
    with walk.container.new_unit_of_work() as uow:
        promotions = uow.promotions.list_for_cycle(walk.plan_id, walk.cycle_id)
        current_plan = uow.plans.get(walk.plan_id)

    current_cycle = next(item for item in current_plan.cycles if item.id == walk.cycle_id)
    promoted_goal_ids = [
        goal.id for goal in current_cycle.goals if goal.status.value == "done"
    ]
    assert [item.goal_id for item in promotions] == promoted_goal_ids

    for item in promotions:
        assert item.from_ref == f"goal/{item.goal_id}"
        assert item.into_ref == f"cycle/{walk.cycle_id}"
        # The recorded refs must resolve in the REAL repo this walk built, so
        # the naming module and the git adapter cannot drift apart silently.
        _git(walk.repo, "rev-parse", "--verify", item.from_ref)
        _git(walk.repo, "rev-parse", "--verify", item.into_ref)
        _git(walk.repo, "cat-file", "-e", item.merge_sha)

    task = current_cycle.goals[0].tasks[0]
    assert task.status == Status.DONE
    assert task.test_bundle is not None
    assert task.verification_evidence
    files = _git(walk.repo, "ls-tree", "-r", "--name-only", f"cycle/{walk.cycle_id}").splitlines()
    assert any(path.startswith("tests/test_dry_run_") for path in files)
    assert any(path.startswith(".orchestrator/dry-run/") for path in files)


def test_cyclic_task_success_clears_the_runtime_circuit(tmp_path, monkeypatch) -> None:
    """A successful task must reset the provider's rate-limit circuit.

    `clear_runtime_circuit` lived ONLY in `_finalize_success` -- the legacy
    (non-cyclic) finalizer. A cyclic plan returns earlier, through
    `_finalize_test_author` / `_finalize_verified_implementation`, so
    `failure_count` only ever grew: transient rate limits accumulated across a
    whole plan run until the circuit latched `manual_intervention` and opened a
    provider_capacity block, even though the provider had recovered in between.
    Only a human `wait_and_retry` could reset it.

    The agent is seeded provider-bound (the clear is guarded on
    provider_id/model_id) while `agent_runner.mode` stays dry-run, so execution
    is deterministic but the spec still carries the circuit key.
    """
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path))
    monkeypatch.setenv("ORCHESTRATOR_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("CIRCUIT_TEST_KEY", "sk-not-a-real-key")
    container = AppContainer(orchestrator_home=tmp_path)
    Base.metadata.create_all(container.engine)
    seeded = CliRunner().invoke(
        cli,
        [
            "seed",
            "demo",
            "--provider",
            "openrouter",
            "--model",
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            "--api-key-env",
            "CIRCUIT_TEST_KEY",
        ],
    )
    assert seeded.exit_code == 0, seeded.output
    # The provider-bound seed also points the REASONER at the live provider;
    # planning must stay deterministic here -- only the agent spec's circuit
    # key matters for this test.
    container.config_store.set("orchestrator", "reasoner.mode", "stub")
    spec = container.agent_repo.get(container.agent_repo.default_agent_id())
    assert spec.provider_id and spec.model_id, "spec must carry the circuit key"

    repo = tmp_path / "project"
    _init_trunk_repo(repo)
    container.project_repo.add(
        ProjectDefinition(id="project-1", name="Project", repo_url=str(repo))
    )
    plan = Plan(
        id="plan-1",
        project_id="project-1",
        brief="clear the circuit on success",
        status=PlanStatus.IDLE,
    )
    with container.new_unit_of_work() as uow:
        uow.plans.save(plan)

    proposal = propose_intent(
        plan.id,
        objective="clear the circuit on success",
        scope=["."],
        constraints=["deterministic"],
        exclusions=[],
        kind=ProposalKind.INITIAL,
        planner_session_ref=None,
        uow=container.new_unit_of_work(),
        clock=container.clock,
    )
    with container.new_unit_of_work() as uow:
        waiting = uow.plans.get(plan.id)
    approve_intent(
        plan.id,
        waiting.review_gate.id,
        proposal.revision,
        container.new_unit_of_work(),
        container.clock,
    )

    planning = PlanningHandler(
        container.reasoner,
        container.agent_repo,
        container.capability_repo,
        container.clock,
    )

    async def drive() -> tuple[str, int]:
        return await drive_plan(
            plan.id,
            container.new_unit_of_work(),
            container.agent_runner,
            container.agent_repo,
            container.workspace,
            container.agent_event_sink,
            container.clock,
            "worker-1",
            planning_handler=planning,
            verifier=container.verification_executor,
        )

    asyncio.run(drive())
    with container.new_unit_of_work() as uow:
        drafted = uow.plans.get(plan.id)
    activate_cycle(
        plan.id,
        drafted.review_gate.id,
        drafted.cycle_draft.revision,
        container.new_unit_of_work(),
        container.clock,
    )
    asyncio.run(drive())
    with container.new_unit_of_work() as uow:
        enriched = uow.plans.get(plan.id)
    goal_id = enriched.active_cycle.goals[0].id

    # An earlier transient rate limit left a half-open circuit behind.
    with container.new_unit_of_work() as uow:
        uow.executions.upsert_runtime_circuit(
            RuntimeCircuit(
                runtime=spec.runtime_type,
                provider_id=spec.provider_id,
                model_id=spec.model_id,
                failure_count=2,
                opened_at=container.clock.now(),
                retry_at=container.clock.now(),
                last_failure_kind="rate_limit",
                safe_message="Upstream error from Nvidia: ResourceExhausted",
                manual_intervention=False,
            )
        )

    assert claim_ready_goal(container.new_unit_of_work(), "worker-1", 60, container.clock) == (
        plan.id,
        goal_id,
    )
    asyncio.run(
        drive_goal(
            plan.id,
            goal_id,
            container.new_unit_of_work(),
            container.agent_runner,
            container.agent_repo,
            container.workspace,
            container.agent_event_sink,
            container.clock,
            "worker-1",
            verifier=container.verification_executor,
        )
    )

    with container.new_unit_of_work() as uow:
        task = uow.plans.get(plan.id).active_cycle.goals[0].tasks[0]
        assert task.status == Status.DONE, "precondition: the task must have succeeded"
        circuit = uow.executions.get_runtime_circuit(
            spec.runtime_type, spec.provider_id, spec.model_id
        )
    assert circuit is None, (
        "a successful cyclic task must clear the provider circuit; leaving it "
        "armed ratchets failure_count toward manual_intervention across a run"
    )
