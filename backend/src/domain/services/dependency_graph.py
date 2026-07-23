"""Shared DAG algorithms over a plain `key -> predecessor keys` edge map.

Deliberately generic (no dependency on Goal/GoalOutline): a list of
predecessor ids per node is already a complete adjacency-list DAG
representation, so this module owns the two algorithms that shape needs —
cycle detection and ready-set computation — once, rather than each call
site re-deriving its own traversal. `CycleDraft.validate_dependencies`
(planning_artifacts.py) and `navigation.ready_goal_ids` both build their own
edge map from their own entity's `depends_on` field and call these pure
functions; neither owns graph traversal itself (domain unfreeze #12).
"""

from __future__ import annotations


def validate_acyclic(keys: list[str], edges: dict[str, list[str]]) -> None:
    """Raise ValueError if `edges` (restricted to `keys`) contains a cycle.

    `edges[key]` is the list of keys `key` depends on. Every key in `keys`
    must appear in `edges` (callers build the map before calling this)."""
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(key: str) -> None:
        if key in visiting:
            raise ValueError("cycle draft dependencies must be acyclic")
        if key in visited:
            return
        visiting.add(key)
        for dependency in edges[key]:
            visit(dependency)
        visiting.remove(key)
        visited.add(key)

    for key in keys:
        visit(key)


def ready_nodes(node_ids: set[str], edges: dict[str, list[str]], done_ids: set[str]) -> set[str]:
    """Every node in `node_ids` whose entire predecessor list is in `done_ids`.

    A node with no entry in `edges` is treated as having no dependencies
    (immediately ready). This is a pure, stateless computation — call it
    fresh on every scan, never cache the result."""
    return {
        node_id
        for node_id in node_ids
        if all(dependency in done_ids for dependency in edges.get(node_id, []))
    }


def blocked_nodes(
    node_ids: set[str], edges: dict[str, list[str]], directly_blocked_ids: set[str]
) -> set[str]:
    """Fixed-point closure of "directly blocked, or depends on something blocked"
    over `node_ids` (domain unfreeze #13 — per-goal blocks). `edges[node_id]` is
    the list of ids `node_id` depends on, same convention as `ready_nodes`/
    `validate_acyclic`. A node outside `node_ids` (e.g. already DONE) never
    propagates blockage — only entries in `directly_blocked_ids` seed it.

    Pure and stateless like its siblings in this module; call fresh, never
    cache. Since the graph is already validated acyclic (`validate_acyclic`),
    a single forward pass over `node_ids` in any order reaches the fixed
    point in at most `len(node_ids)` iterations; we just iterate to a stable
    result rather than assume a particular traversal order."""
    blocked = {node_id for node_id in directly_blocked_ids if node_id in node_ids}
    changed = True
    while changed:
        changed = False
        for node_id in node_ids:
            if node_id in blocked:
                continue
            if any(dependency in blocked for dependency in edges.get(node_id, [])):
                blocked.add(node_id)
                changed = True
    return blocked
