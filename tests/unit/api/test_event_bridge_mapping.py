"""
tests/unit/api/test_event_bridge_mapping.py — domain-event → SSE mapping.
"""
from __future__ import annotations

from src.api.event_bridge import map_domain_event_to_sse


def test_task_unassignable_maps_to_sse_with_capability():
    mapped = map_domain_event_to_sse(
        "task.unassignable",
        {"task_id": "t-1", "required_capability": "code:backend", "reason": "No active agent"},
    )
    assert mapped is not None
    sse_type, payload = mapped
    assert sse_type == "task.unassignable"
    assert payload["task_id"] == "t-1"
    assert payload["required_capability"] == "code:backend"
    assert payload["reason"] == "No active agent"


def test_task_assigned_still_maps_to_status_changed():
    mapped = map_domain_event_to_sse("task.assigned", {"task_id": "t-1"})
    assert mapped == ("task.status_changed", {"task_id": "t-1", "status": "assigned"})


def test_task_progress_maps_with_lines():
    mapped = map_domain_event_to_sse(
        "task.progress", {"task_id": "t-1", "lines": ["building…", "done step 1"], "ts": 123.0}
    )
    assert mapped is not None
    sse_type, payload = mapped
    assert sse_type == "task.progress"
    assert payload["task_id"] == "t-1"
    assert payload["lines"] == ["building…", "done step 1"]
