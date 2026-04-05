"""
Unit tests for StreamingPlannerCallback.
"""
from unittest.mock import MagicMock, call
import pytest

from src.infra.logging.planner_callback import StreamingPlannerCallback


def _make_callback():
    planner_log = MagicMock()
    callback = StreamingPlannerCallback(planner_log)
    return callback, planner_log


class TestStreamingPlannerCallback:

    def test_on_turn_with_text_block_calls_on_turn(self):
        callback, planner_log = _make_callback()
        blocks = [{"type": "text", "text": "Hello, this is the planner."}]

        callback.on_turn("assistant", blocks)

        planner_log.on_turn.assert_called_once_with("assistant", blocks)

    def test_on_turn_with_tool_use_block_calls_on_tool_call(self):
        callback, planner_log = _make_callback()
        blocks = [
            {"type": "tool_use", "name": "propose_decision", "input": {"id": "use-redis", "domain": "infra"}},
        ]

        callback.on_turn("assistant", blocks)

        planner_log.on_tool_call.assert_called_once_with(
            tool_name="propose_decision",
            args={"id": "use-redis", "domain": "infra"},
        )

    def test_on_turn_with_tool_result_block_calls_on_tool_result(self):
        callback, planner_log = _make_callback()
        blocks = [
            {"type": "tool_result", "tool_use_id": "tool-abc", "content": '{"accepted": true}'},
        ]

        callback.on_turn("user", blocks)

        planner_log.on_tool_result.assert_called_once_with(
            tool_name="tool-abc",
            result_json='{"accepted": true}',
        )

    def test_on_turn_with_tool_result_list_content(self):
        callback, planner_log = _make_callback()
        blocks = [
            {
                "type": "tool_result",
                "tool_use_id": "tool-xyz",
                "content": [{"type": "text", "text": "accepted"}],
            }
        ]

        callback.on_turn("user", blocks)

        planner_log.on_tool_result.assert_called_once()
        call_kwargs = planner_log.on_tool_result.call_args
        assert call_kwargs[1]["tool_name"] == "tool-xyz"
        assert "accepted" in call_kwargs[1]["result_json"]

    def test_mixed_content_blocks_produce_correct_sequence(self):
        callback, planner_log = _make_callback()
        blocks = [
            {"type": "text", "text": "I will propose a decision."},
            {"type": "tool_use", "name": "propose_decision", "input": {"id": "use-pg", "domain": "db"}},
        ]

        callback.on_turn("assistant", blocks)

        planner_log.on_turn.assert_called_once()
        planner_log.on_tool_call.assert_called_once_with(
            tool_name="propose_decision",
            args={"id": "use-pg", "domain": "db"},
        )

    def test_non_dict_blocks_are_skipped_gracefully(self):
        callback, planner_log = _make_callback()
        blocks = ["plain string", 42, None, {"type": "text", "text": "ok"}]

        # Should not raise
        callback.on_turn("assistant", blocks)
        planner_log.on_turn.assert_called_once()
