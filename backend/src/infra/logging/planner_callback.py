"""
src/infra/logging/planner_callback.py — Real-time session callback bridge.

Converts raw (role, content_blocks) turn callbacks from the PlannerRuntime
into structured PlannerLiveLogger events for terminal streaming and JSONL
file logging.

Wrapped by the CLI commands and injected into PlannerOrchestrator via the
`turn_callback` mechanism added in Step 4.
"""
from __future__ import annotations

from src.infra.logging.planner_logger import PlannerLiveLogger


class StreamingPlannerCallback:
    """
    Translates raw runtime turn callbacks into structured log events.

    Usage (in CLI command):
        planner_log = PlannerLiveLogger(live_logger, session_id, mode, log_dir)
        callback = StreamingPlannerCallback(planner_log)
        orchestrator.set_turn_callback(callback.on_turn)
    """

    def __init__(self, planner_logger: PlannerLiveLogger) -> None:
        self._log = planner_logger

    def on_turn(self, role: str, content_blocks: list) -> None:
        """
        Called by PlannerOrchestrator._make_session_callback on each turn.

        Extracts tool_use and tool_result blocks for granular visibility,
        then delegates to PlannerLiveLogger for rendering and file logging.
        """
        self._log.on_turn(role, content_blocks)

        # Scan for tool_use blocks to emit per-tool events.
        for block in content_blocks:
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")
            if btype == "tool_use":
                self._log.on_tool_call(
                    tool_name=block.get("name", "unknown"),
                    args=block.get("input", {}),
                )
            elif btype == "tool_result":
                content = block.get("content", "")
                if isinstance(content, list):
                    content = " ".join(
                        c.get("text", "") for c in content if isinstance(c, dict)
                    )
                self._log.on_tool_result(
                    tool_name=block.get("tool_use_id", "unknown"),
                    result_json=str(content)[:200],
                )
