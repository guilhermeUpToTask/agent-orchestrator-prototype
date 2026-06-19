"""
tests/unit/domain/test_roadmap.py — Roadmap value object invariants.
"""
import pytest

from src.domain.value_objects.goal import GoalSpec, GoalTaskDef, Roadmap


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def make_task(task_id: str = "t1", capability: str = "python", depends_on=None) -> GoalTaskDef:
    return GoalTaskDef(
        task_id=task_id,
        title=f"Task {task_id}",
        description="desc",
        capability=capability,
        depends_on=depends_on or [],
    )


def make_spec(
    name: str,
    depends_on=None,
    feature_tag=None,
    tasks=None,
) -> GoalSpec:
    return GoalSpec(
        name=name,
        description=f"Goal {name}",
        tasks=tasks or [make_task()],
        depends_on=depends_on or [],
        feature_tag=feature_tag,
    )


# ---------------------------------------------------------------------------
# Construction — happy path
# ---------------------------------------------------------------------------

def test_single_goal_roadmap():
    r = Roadmap(goals=[make_spec("setup")])
    assert len(r) == 1


def test_two_goals_with_dependency():
    setup = make_spec("setup")
    api = make_spec("api", depends_on=["setup"])
    r = Roadmap(goals=[setup, api])
    order = r.topological_order()
    names = [g.name for g in order]
    assert names.index("setup") < names.index("api")


def test_goals_by_feature():
    a = make_spec("a", feature_tag="auth")
    b = make_spec("b", feature_tag="auth")
    c = make_spec("c", feature_tag="infra")
    r = Roadmap(goals=[a, b, c])
    groups = r.goals_by_feature()
    assert set(g.name for g in groups["auth"]) == {"a", "b"}
    assert set(g.name for g in groups["infra"]) == {"c"}


def test_goal_names():
    r = Roadmap(goals=[make_spec("x"), make_spec("y")])
    assert r.goal_names() == {"x", "y"}


# ---------------------------------------------------------------------------
# Validation failures
# ---------------------------------------------------------------------------

def test_empty_goals_raises():
    with pytest.raises(ValueError, match="at least one goal"):
        Roadmap(goals=[])


def test_duplicate_goal_names_raises():
    with pytest.raises(ValueError, match="Duplicate"):
        Roadmap(goals=[make_spec("dup"), make_spec("dup")])


def test_dangling_depends_on_raises():
    spec = make_spec("api", depends_on=["nonexistent"])
    with pytest.raises(ValueError, match="nonexistent"):
        Roadmap(goals=[spec])


def test_cycle_raises():
    a = make_spec("a", depends_on=["b"])
    b = make_spec("b", depends_on=["a"])
    with pytest.raises(ValueError, match="cycle"):
        Roadmap(goals=[a, b])


def test_cross_feature_dependency_raises():
    auth = make_spec("auth-goal", feature_tag="auth")
    infra = make_spec("infra-goal", feature_tag="infra", depends_on=["auth-goal"])
    with pytest.raises(ValueError, match="Cross-feature"):
        Roadmap(goals=[auth, infra])


# ---------------------------------------------------------------------------
# topological_order
# ---------------------------------------------------------------------------

def test_topological_order_respects_deps():
    a = make_spec("a")
    b = make_spec("b", depends_on=["a"])
    c = make_spec("c", depends_on=["b"])
    r = Roadmap(goals=[c, b, a])  # deliberately out of order
    order = [g.name for g in r.topological_order()]
    assert order.index("a") < order.index("b") < order.index("c")


def test_topological_order_no_deps_preserves_declaration_order():
    names = ["alpha", "beta", "gamma"]
    r = Roadmap(goals=[make_spec(n) for n in names])
    order = [g.name for g in r.topological_order()]
    assert order == names


# ---------------------------------------------------------------------------
# GoalSpec internal task DAG validation
# ---------------------------------------------------------------------------

def test_goalspec_rejects_unknown_task_dep():
    with pytest.raises(ValueError, match="nonexistent"):
        GoalSpec(
            name="g",
            description="d",
            tasks=[make_task("t1", depends_on=["nonexistent"])],
        )


def test_goalspec_rejects_task_cycle():
    t1 = make_task("t1", depends_on=["t2"])
    t2 = make_task("t2", depends_on=["t1"])
    with pytest.raises(ValueError, match="cycle"):
        GoalSpec(name="g", description="d", tasks=[t1, t2])


def test_goalspec_accepts_empty_tasks_jit_mode():
    # Empty task list is valid — Tactical JIT Planner fills them in later.
    spec = GoalSpec(name="g", description="d", tasks=[])
    assert spec.tasks == []
