"""
tests/unit/domain/test_planner_session.py — PlannerSession aggregate tests.

Tests: state machine transitions, _bump() on every mutation,
       guard conditions, has_valid_roadmap().
"""
import pytest

from src.domain.aggregates.planner_session import PlannerSession, PlannerSessionStatus


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def make_session(user_input: str = "add oauth login") -> PlannerSession:
    return PlannerSession.create(user_input)


# ---------------------------------------------------------------------------
# create()
# ---------------------------------------------------------------------------

def test_create_defaults():
    s = make_session()
    assert s.status == PlannerSessionStatus.PENDING
    assert s.user_input == "add oauth login"
    assert s.session_id.startswith("plan-")
    assert s.state_version == 1
    assert s.turns == []
    assert s.history == []


# ---------------------------------------------------------------------------
# start()
# ---------------------------------------------------------------------------

def test_start_transitions_to_running():
    s = make_session()
    s.start()
    assert s.status == PlannerSessionStatus.RUNNING


def test_start_bumps():
    s = make_session()
    v_before = s.state_version
    s.start()
    assert s.state_version == v_before + 1
    assert any(e.event == "planner.session_started" for e in s.history)


def test_start_from_wrong_state_raises():
    s = make_session()
    s.start()
    with pytest.raises(ValueError, match="pending"):
        s.start()


# ---------------------------------------------------------------------------
# add_turn()
# ---------------------------------------------------------------------------

def test_add_turn_appends_and_bumps():
    s = make_session()
    s.start()
    v = s.state_version
    s.add_turn("assistant", [{"type": "text", "text": "hello"}], turn_index=0)
    assert len(s.turns) == 1
    assert s.turns[0].role == "assistant"
    assert s.turns[0].turn_index == 0
    assert s.state_version == v + 1


def test_add_turn_requires_running():
    s = make_session()
    with pytest.raises(ValueError):
        s.add_turn("assistant", [], 0)


# ---------------------------------------------------------------------------
# record_roadmap_candidate()
# ---------------------------------------------------------------------------

def test_record_roadmap_candidate_sets_data():
    s = make_session()
    s.start()
    s.record_roadmap_candidate({"goals": []})
    assert s.roadmap_data == {"goals": []}


def test_record_roadmap_candidate_bumps():
    s = make_session()
    s.start()
    v = s.state_version
    s.record_roadmap_candidate({"goals": []})
    assert s.state_version == v + 1


# ---------------------------------------------------------------------------
# complete()
# ---------------------------------------------------------------------------

def test_complete_happy_path():
    s = make_session()
    s.start()
    s.record_roadmap_candidate({"goals": ["x"]})
    s.complete(
        reasoning="all good",
        raw_llm_output="{}",
        validation_errors=[],
        validation_warnings=["minor warning"],
    )
    assert s.status == PlannerSessionStatus.COMPLETED
    assert s.reasoning == "all good"
    assert s.validation_warnings == ["minor warning"]


def test_complete_bumps():
    s = make_session()
    s.start()
    s.record_roadmap_candidate({"goals": []})
    v = s.state_version
    s.complete("", "{}", [], [])
    assert s.state_version == v + 1
    assert any(e.event == "planner.session_completed" for e in s.history)


def test_complete_without_roadmap_raises():
    s = make_session()
    s.start()
    with pytest.raises(ValueError, match="roadmap_data"):
        s.complete("", "{}", [], [])


def test_complete_from_wrong_state_raises():
    s = make_session()
    with pytest.raises(ValueError):
        s.complete("", "{}", [], [])


# ---------------------------------------------------------------------------
# fail()
# ---------------------------------------------------------------------------

def test_fail_transitions():
    s = make_session()
    s.start()
    s.fail("something broke")
    assert s.status == PlannerSessionStatus.FAILED
    assert s.failure_reason == "something broke"


def test_fail_bumps():
    s = make_session()
    s.start()
    v = s.state_version
    s.fail("oops")
    assert s.state_version == v + 1
    assert any(e.event == "planner.session_failed" for e in s.history)


def test_fail_from_wrong_state_raises():
    s = make_session()
    with pytest.raises(ValueError):
        s.fail("too early")


# ---------------------------------------------------------------------------
# record_goal_dispatched / record_dispatch_failure
# ---------------------------------------------------------------------------

def test_record_goal_dispatched():
    s = make_session()
    s.start()
    s.record_roadmap_candidate({})
    s.complete("", "{}", [], [])
    # can record after completion (no status guard on this method)
    s.record_goal_dispatched("goal-abc", "my-feature")
    assert "goal-abc" in s.goals_dispatched
    assert any(
        e.event == "planner.goal_dispatched" and e.detail.get("goal_name") == "my-feature"
        for e in s.history
    )


def test_record_dispatch_failure_appends_error():
    s = make_session()
    s.start()
    s.record_dispatch_failure("broken-goal", "repo error")
    assert any("broken-goal" in e for e in s.validation_errors)


# ---------------------------------------------------------------------------
# is_terminal / has_valid_roadmap
# ---------------------------------------------------------------------------

def test_is_terminal_pending():
    assert not make_session().is_terminal()


def test_is_terminal_completed():
    s = make_session()
    s.start()
    s.record_roadmap_candidate({})
    s.complete("", "{}", [], [])
    assert s.is_terminal()


def test_is_terminal_failed():
    s = make_session()
    s.start()
    s.fail("reason")
    assert s.is_terminal()


def test_has_valid_roadmap_true():
    s = make_session()
    s.start()
    s.record_roadmap_candidate({"goals": ["x"]})
    s.complete("", "{}", [], [])
    assert s.has_valid_roadmap()


def test_has_valid_roadmap_false_with_errors():
    s = make_session()
    s.start()
    s.record_roadmap_candidate({"goals": []})
    s.complete("", "{}", ["error!"], [])
    assert not s.has_valid_roadmap()


def test_has_valid_roadmap_false_no_data():
    s = make_session()
    assert not s.has_valid_roadmap()


# ---------------------------------------------------------------------------
# Aggregate convention: mutations must go through methods
# ---------------------------------------------------------------------------

def test_state_version_only_changes_via_bump():
    """All mutations increment state_version — direct write is discouraged by convention."""
    s = make_session()
    initial_version = s.state_version
    s.start()
    assert s.state_version == initial_version + 1
    s.add_turn("assistant", [], 0)
    assert s.state_version == initial_version + 2
