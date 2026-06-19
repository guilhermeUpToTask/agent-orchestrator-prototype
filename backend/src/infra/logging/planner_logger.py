"""
src/infra/logging/planner_logger.py — Planning-layer observability façade.

Wraps LiveLogger and provides a high-level API for CLI commands and the
StreamingPlannerCallback to emit structured planning events.

All terminal rendering is handled by LiveLogger; this class only constructs
the correct LogEvent objects and routes them to the shared logger.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from src.infra.logging.live_logger import LiveLogger
from src.infra.logging.log_events import (
    build_planner_session_start_event,
    build_planner_turn_event,
    build_planner_tool_call_event,
    build_planner_tool_result_event,
    build_planner_session_end_event,
    build_planner_decision_event,
    build_planner_phase_event,
    build_jit_plan_start_event,
    build_jit_plan_end_event,
    build_goal_dispatched_event,
)


PLANNER_AGENT_NAME = "planner"


class PlannerLiveLogger:
    """
    Observability façade for the planning layer.

    Instantiate once per planning command invocation and pass to
    StreamingPlannerCallback. The CLI commands own the lifecycle
    (start / end).
    """

    def __init__(
        self,
        live_logger: LiveLogger,
        session_id: str,
        mode: str,
        log_dir: Optional[Path] = None,
    ) -> None:
        self._logger = live_logger
        self._session_id = session_id
        self._mode = mode
        self._start_time = time.monotonic()
        self._turn_count = 0

        self._logger.register_agent(
            agent_name=PLANNER_AGENT_NAME,
            session_id=session_id,
            workspace_path=str(log_dir or "."),
        )

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def session_start(self) -> None:
        self._start_time = time.monotonic()
        self._logger.log_event(
            build_planner_session_start_event(self._mode, self._session_id)
        )

    def session_end(self, success: bool) -> None:
        elapsed = time.monotonic() - self._start_time
        self._logger.log_event(
            build_planner_session_end_event(
                self._mode, success, elapsed, self._turn_count
            )
        )

    # ------------------------------------------------------------------
    # Turn streaming
    # ------------------------------------------------------------------

    def on_turn(self, role: str, content_blocks: list) -> None:
        """Called by StreamingPlannerCallback after each LLM turn."""
        self._turn_count += 1
        preview = _extract_text_preview(content_blocks)
        self._logger.log_event(
            build_planner_turn_event(role, preview, self._turn_count)
        )

    # ------------------------------------------------------------------
    # Tool visibility
    # ------------------------------------------------------------------

    def on_tool_call(self, tool_name: str, args: dict) -> None:
        args_preview = str(args)[:120]
        self._logger.log_event(
            build_planner_tool_call_event(tool_name, args_preview)
        )

    def on_tool_result(self, tool_name: str, result_json: str) -> None:
        import json as _json
        try:
            parsed = _json.loads(result_json)
            accepted = bool(parsed.get("accepted", parsed.get("proposed", True)))
        except Exception:
            accepted = True
        self._logger.log_event(
            build_planner_tool_result_event(tool_name, accepted, result_json[:120])
        )

    # ------------------------------------------------------------------
    # Domain events
    # ------------------------------------------------------------------

    def on_decision_proposed(self, decision_id: str, domain: str) -> None:
        self._logger.log_event(
            build_planner_decision_event(decision_id, domain)
        )

    def on_phase_proposed(self, phase_name: str, goal_names: list) -> None:
        self._logger.log_event(
            build_planner_phase_event(phase_name, goal_names)
        )

    def on_goal_dispatched(self, goal_id: str, goal_name: str, phase_index: int) -> None:
        self._logger.log_event(
            build_goal_dispatched_event(goal_id, goal_name, phase_index)
        )

    def on_jit_start(self, goal_id: str, goal_name: str) -> None:
        self._logger.log_event(
            build_jit_plan_start_event(goal_id, goal_name)
        )

    def on_jit_end(self, goal_id: str, task_ids: list) -> None:
        elapsed = time.monotonic() - self._start_time
        self._logger.log_event(
            build_jit_plan_end_event(goal_id, task_ids, elapsed)
        )

    def close(self) -> None:
        self._logger.close_agent(PLANNER_AGENT_NAME)


def _extract_text_preview(content_blocks: list, max_len: int = 200) -> str:
    """Extract a readable text preview from a list of Anthropic content blocks."""
    parts = []
    for block in content_blocks:
        if isinstance(block, dict):
            text = block.get("text", "")
            if text:
                parts.append(text)
    combined = " ".join(parts).replace("\n", " ")
    return combined[:max_len] + "…" if len(combined) > max_len else combined
