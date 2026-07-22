from datetime import datetime, timezone
from src.domain.entities.agent_spec import AgentSpec
from src.domain.entities.execution_contracts import (
    ContractCriterion,
    TaskContract,
    TestBundle,
    VerificationStrategy,
)
from src.domain.entities.task import Task
from src.domain.policies.retry_policies import RetryPolicy
from src.infra.runtime.cli_runner import build_task_prompt


def spec(role="implementer"):
    return AgentSpec(
        id="a1",
        name="A",
        role=role,
        model_role="smart",
        instructions="Be precise.",
        default_retry=RetryPolicy(),
    )


def contract():
    return TaskContract(
        id="c1",
        position=0,
        objective="Add behavior.",
        acceptance_criteria=[
            ContractCriterion(id="AC-1", description="Correct."),
            ContractCriterion(id="AC-2", description="Covered."),
        ],
        goal_criterion_ids=["AC-1"],
        allowed_scope=["src/feature.py", "tests/test_feature.py"],
        forbidden_scope=["src/other.py"],
        verification_commands=["pytest tests/test_feature.py", "ruff check src"],
        verification_strategy=VerificationStrategy.TDD,
    )


def test_contractless_prompt_is_unchanged():
    task = Task(
        id="t1",
        name="Example",
        position=0,
        description="Do it.",
        required_capabilities=["python"],
        attempt=2,
    )
    assert (
        build_task_prompt(task, spec())
        == "# Task: Example\n\nDo it.\n\n## Your role\nimplementer\n\n## Instructions\nBe precise.\n\n## Required capabilities\npython\n\n---\nTask ID: `t1` | Attempt: 2"
    )


def test_contract_prompt_renders_constraints():
    p = build_task_prompt(
        Task(id="t1", name="Example", position=0, description="Do it.", contract=contract()),
        spec("test_author"),
    )
    assert (
        "## Constraints" in p
        and "Objective: Add behavior." in p
        and "`AC-1`: Correct." in p
        and "`src/other.py`" in p
        and "`pytest tests/test_feature.py`" in p
    )
    assert (
        "the orchestrator runs these independently" in p
        and "self-reported results are ignored" in p
        and "Verification strategy: `tdd`" in p
    )


def test_test_author_stage_is_tests_only():
    p = build_task_prompt(
        Task(id="t1", name="Write tests", position=0, description="Cover it.", contract=contract()),
        spec("test_author"),
    )
    assert "Write ONLY tests that fail for the right reason; never modify production files." in p


def test_implementer_stage_does_not_touch_tests():
    t = Task(id="t1", name="Implement", position=0, description="Pass tests.", contract=contract())
    t.test_bundle = _valid_bundle()
    assert "Make the frozen tests pass; never modify tests." in build_task_prompt(t, spec())


def _valid_bundle() -> TestBundle:
    return TestBundle(
        task_id="t1",
        task_revision=1,
        test_commit_sha="abc",
        protected_file_hashes={},
        criterion_to_tests={"AC-1": ["tests/test_feature.py"], "AC-2": ["tests/test_feature.py"]},
        verification_strategy=VerificationStrategy.TDD,
        red_or_baseline_evidence_refs=["e"],
        frozen_at=datetime.now(timezone.utc),
    )


def test_run_role_overrides_static_role_in_test_authoring_stage():
    # Regression (walkthrough Bug B): an implementer-role agent performing a
    # test-authoring run must be told it is the test_author — matching the stage
    # expectation — not "Your role: implementer / Implement the task".
    t = Task(id="t1", name="Write tests", position=0, description="Cover it.", contract=contract())
    p = build_task_prompt(t, spec("implementer"))
    assert "## Your role\ntest_author\n" in p
    assert "## Your role\nimplementer" not in p
    assert "Write ONLY tests that fail for the right reason; never modify production files." in p


def test_run_role_overrides_static_role_in_implementation_stage():
    # Symmetric: a test_author-role agent on the implementation stage plays the
    # implementer role (a validated test bundle exists).
    t = Task(id="t1", name="Implement", position=0, description="Pass tests.", contract=contract())
    t.test_bundle = _valid_bundle()
    p = build_task_prompt(t, spec("test_author"))
    assert "## Your role\nimplementer\n" in p
    assert "Make the frozen tests pass; never modify tests." in p
