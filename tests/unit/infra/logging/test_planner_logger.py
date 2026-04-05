"""
Unit tests for PlannerLiveLogger.
"""
from unittest.mock import MagicMock, patch
import pytest

from src.infra.logging.live_logger import LiveLogger
from src.infra.logging.log_events import LogEventType
from src.infra.logging.planner_logger import PlannerLiveLogger


def _make_logger(tmp_path):
    live = LiveLogger(json_log_dir=tmp_path)
    return PlannerLiveLogger(live, session_id="test-session", mode="architecture", log_dir=tmp_path), live


class TestPlannerLiveLoggerSessionLifecycle:

    def test_session_start_logs_planner_session_start(self, tmp_path):
        planner_log, live = _make_logger(tmp_path)
        logged = []
        original_log = live.log_event
        live.log_event = lambda e: (logged.append(e), original_log(e))

        planner_log.session_start()

        assert any(e.event_type == LogEventType.PLANNER_SESSION_START for e in logged)

    def test_session_end_success_logs_planner_session_end(self, tmp_path):
        planner_log, live = _make_logger(tmp_path)
        logged = []
        original_log = live.log_event
        live.log_event = lambda e: (logged.append(e), original_log(e))

        planner_log.session_end(success=True)

        end_events = [e for e in logged if e.event_type == LogEventType.PLANNER_SESSION_END]
        assert len(end_events) == 1
        assert end_events[0].details["success"] is True

    def test_session_end_failure_sets_success_false(self, tmp_path):
        planner_log, live = _make_logger(tmp_path)
        logged = []
        original_log = live.log_event
        live.log_event = lambda e: (logged.append(e), original_log(e))

        planner_log.session_end(success=False)

        end_events = [e for e in logged if e.event_type == LogEventType.PLANNER_SESSION_END]
        assert len(end_events) == 1
        assert end_events[0].details["success"] is False


class TestPlannerLiveLoggerTurns:

    def test_on_turn_increments_turn_count(self, tmp_path):
        planner_log, live = _make_logger(tmp_path)
        live.log_event = MagicMock()

        assert planner_log._turn_count == 0
        planner_log.on_turn("assistant", [{"type": "text", "text": "hello"}])
        assert planner_log._turn_count == 1
        planner_log.on_turn("user", [{"type": "text", "text": "ok"}])
        assert planner_log._turn_count == 2

    def test_on_turn_logs_planner_turn_event(self, tmp_path):
        planner_log, live = _make_logger(tmp_path)
        logged = []
        live.log_event = lambda e: logged.append(e)

        planner_log.on_turn("assistant", [{"type": "text", "text": "hello"}])

        turn_events = [e for e in logged if e.event_type == LogEventType.PLANNER_TURN]
        assert len(turn_events) == 1
        assert turn_events[0].details["role"] == "assistant"
        assert turn_events[0].details["turn_index"] == 1


class TestPlannerLiveLoggerTools:

    def test_on_tool_call_logs_planner_tool_call(self, tmp_path):
        planner_log, live = _make_logger(tmp_path)
        logged = []
        live.log_event = lambda e: logged.append(e)

        planner_log.on_tool_call("propose_decision", {"id": "use-fastapi", "domain": "backend"})

        tool_events = [e for e in logged if e.event_type == LogEventType.PLANNER_TOOL_CALL]
        assert len(tool_events) == 1
        assert tool_events[0].details["tool_name"] == "propose_decision"

    def test_on_tool_result_accepted_true(self, tmp_path):
        planner_log, live = _make_logger(tmp_path)
        logged = []
        live.log_event = lambda e: logged.append(e)

        planner_log.on_tool_result("propose_decision", '{"accepted": true}')

        result_events = [e for e in logged if e.event_type == LogEventType.PLANNER_TOOL_RESULT]
        assert len(result_events) == 1
        assert result_events[0].details["accepted"] is True


class TestPlannerLiveLoggerDomainEvents:

    def test_on_decision_proposed_logs_planner_decision(self, tmp_path):
        planner_log, live = _make_logger(tmp_path)
        logged = []
        live.log_event = lambda e: logged.append(e)

        planner_log.on_decision_proposed("use-fastapi", "backend")

        decision_events = [e for e in logged if e.event_type == LogEventType.PLANNER_DECISION]
        assert len(decision_events) == 1
        assert decision_events[0].details["decision_id"] == "use-fastapi"
        assert decision_events[0].details["domain"] == "backend"

    def test_on_phase_proposed_logs_planner_phase(self, tmp_path):
        planner_log, live = _make_logger(tmp_path)
        logged = []
        live.log_event = lambda e: logged.append(e)

        planner_log.on_phase_proposed("Foundation", ["setup-db", "setup-auth"])

        phase_events = [e for e in logged if e.event_type == LogEventType.PLANNER_PHASE]
        assert len(phase_events) == 1
        assert phase_events[0].details["phase_name"] == "Foundation"
        assert phase_events[0].details["goal_names"] == ["setup-db", "setup-auth"]

    def test_on_goal_dispatched_logs_goal_dispatched(self, tmp_path):
        planner_log, live = _make_logger(tmp_path)
        logged = []
        live.log_event = lambda e: logged.append(e)

        planner_log.on_goal_dispatched("goal-123", "setup-db", phase_index=0)

        dispatched = [e for e in logged if e.event_type == LogEventType.GOAL_DISPATCHED]
        assert len(dispatched) == 1
        assert dispatched[0].details["goal_name"] == "setup-db"

    def test_session_end_includes_elapsed_time(self, tmp_path):
        planner_log, live = _make_logger(tmp_path)
        logged = []
        live.log_event = lambda e: logged.append(e)

        planner_log.session_end(success=True)

        end_events = [e for e in logged if e.event_type == LogEventType.PLANNER_SESSION_END]
        assert end_events[0].details["elapsed_s"] >= 0.0
