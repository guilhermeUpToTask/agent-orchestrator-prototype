"""Shared DAG algorithms (domain unfreeze #13): cycle detection lifted
verbatim out of CycleDraft.validate_dependencies, and the new ready_nodes
primitive goal-parallelism scheduling needs. Pure, plain dict[str, list[str]]
edge maps — no Goal/GoalOutline involved."""

from __future__ import annotations

import pytest

from src.domain.services.dependency_graph import ready_nodes, validate_acyclic


def test_validate_acyclic_accepts_a_dag():
    validate_acyclic(["a", "b", "c"], {"a": [], "b": ["a"], "c": ["a", "b"]})


def test_validate_acyclic_rejects_a_direct_cycle():
    with pytest.raises(ValueError, match="acyclic"):
        validate_acyclic(["a", "b"], {"a": ["b"], "b": ["a"]})


def test_validate_acyclic_rejects_a_longer_cycle():
    with pytest.raises(ValueError, match="acyclic"):
        validate_acyclic(["a", "b", "c"], {"a": ["b"], "b": ["c"], "c": ["a"]})


def test_validate_acyclic_rejects_self_dependency():
    with pytest.raises(ValueError, match="acyclic"):
        validate_acyclic(["a"], {"a": ["a"]})


def test_ready_nodes_returns_all_independently_ready_nodes():
    edges = {"a": [], "b": [], "c": ["a"]}
    assert ready_nodes({"a", "b", "c"}, edges, done_ids=set()) == {"a", "b"}


def test_ready_nodes_diamond_dependency():
    # d depends on b and c, both depend on a; a is done
    edges = {"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]}
    assert ready_nodes({"b", "c", "d"}, edges, done_ids={"a"}) == {"b", "c"}
    # once b and c are also done, d becomes ready
    assert ready_nodes({"d"}, edges, done_ids={"a", "b", "c"}) == {"d"}


def test_ready_nodes_disjoint_components():
    edges = {"a": [], "b": ["a"], "x": [], "y": ["x"]}
    assert ready_nodes({"a", "b", "x", "y"}, edges, done_ids=set()) == {"a", "x"}


def test_ready_nodes_missing_edge_entry_treated_as_no_dependencies():
    assert ready_nodes({"a"}, {}, done_ids=set()) == {"a"}
