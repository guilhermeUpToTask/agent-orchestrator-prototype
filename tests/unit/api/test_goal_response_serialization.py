"""
Regression test for goal serialization.

A goal's HistoryEntry.timestamp is a ``datetime``; the GoalHistoryEntryResponse
DTO must accept it. It previously declared ``timestamp: str``, so building the
response raised a pydantic ValidationError — which, being a ValueError subclass,
surfaced as a misleading 409 on ``GET /api/goals`` and broke the goals view.
"""
from __future__ import annotations

from datetime import datetime

from src.api.routers.goals import _goal_to_response
from src.domain.aggregates.goal import GoalAggregate


def _goal_with_history() -> GoalAggregate:
    goal = GoalAggregate.create(name="my-goal", description="desc", task_summaries=[])
    goal.start()  # appends a HistoryEntry with a datetime timestamp
    assert goal.history, "transition should record history"
    return goal


def test_goal_with_datetime_history_serializes():
    goal = _goal_with_history()

    response = _goal_to_response(goal)

    assert response.goal_id == goal.goal_id
    assert len(response.history) == len(goal.history)
    # The datetime timestamp is preserved (serialized to ISO-8601 in the HTTP body).
    assert isinstance(response.history[0].timestamp, datetime)
