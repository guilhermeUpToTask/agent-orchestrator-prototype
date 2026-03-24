from __future__ import annotations

import hashlib
import time
from typing import Iterator, Optional

from src.app.telemetry.service import TelemetryService
from src.app.telemetry.tracing import TraceContext
from src.domain import (
    AgentExecutionResult,
    AgentProps,
    AgentRuntimePort,
    ExecutionContext,
    PlannerOutput,
    PlannerRuntimeError,
    PlannerRuntimePort,
    PlannerTool,
    SessionHandle,
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class TelemetryAgentRuntimeWrapper(AgentRuntimePort):
    def __init__(
        self,
        wrapped: AgentRuntimePort,
        telemetry: TelemetryService,
        trace_context: TraceContext,
        agent_id: str,
    ) -> None:
        self._wrapped = wrapped
        self._telemetry = telemetry
        self._trace = trace_context
        self._agent_id = agent_id

    def start_session(self, agent_props: AgentProps, workspace_path: str, env_vars: dict[str, str]) -> SessionHandle:
        span = self._telemetry.start_span(self._trace)
        self._telemetry.emit(
            "agent.started",
            span,
            payload={"runtime_type": agent_props.runtime_type, "workspace_path": workspace_path},
            agent_id=self._agent_id,
        )
        return self._wrapped.start_session(agent_props, workspace_path, env_vars)

    def send_execution_payload(self, handle: SessionHandle, context: ExecutionContext) -> None:
        span = self._telemetry.start_span(self._trace)
        prompt_material = f"{context.title}\n{context.description}\n{context.execution.model_dump_json()}"
        self._telemetry.emit(
            "llm.request",
            span,
            payload={"model": getattr(self._wrapped, "_model", "unknown"), "prompt_hash": _sha256(prompt_material)},
            task_id=context.task_id,
            agent_id=self._agent_id,
        )
        self._wrapped.send_execution_payload(handle, context)

    def wait_for_completion(self, handle: SessionHandle, timeout_seconds: int = 600) -> AgentExecutionResult:
        span = self._telemetry.start_span(self._trace)
        start = time.monotonic()
        try:
            result = self._wrapped.wait_for_completion(handle, timeout_seconds)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            self._telemetry.emit(
                "llm.response" if result.success else "llm.error",
                span,
                payload={
                    "model": getattr(self._wrapped, "_model", "unknown"),
                    "latency_ms": elapsed_ms,
                    "success": result.success,
                    "exit_code": result.exit_code,
                    "token_usage": {},
                },
                agent_id=self._agent_id,
            )
            self._telemetry.emit(
                "agent.completed" if result.success else "agent.failed",
                span,
                payload={"elapsed_seconds": result.elapsed_seconds, "exit_code": result.exit_code},
                agent_id=self._agent_id,
            )
            return result
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            self._telemetry.emit(
                "llm.error",
                span,
                payload={"model": getattr(self._wrapped, "_model", "unknown"), "latency_ms": elapsed_ms},
                metadata={"error_type": type(exc).__name__, "message": str(exc)},
                agent_id=self._agent_id,
            )
            self._telemetry.emit(
                "agent.failed",
                span,
                metadata={"error_type": type(exc).__name__, "message": str(exc)},
                agent_id=self._agent_id,
            )
            raise

    def terminate_session(self, handle: SessionHandle) -> None:
        self._wrapped.terminate_session(handle)

    def stream_logs(self, handle: SessionHandle) -> Iterator[str]:
        yield from self._wrapped.stream_logs(handle)


class TelemetryPlannerRuntimeWrapper(PlannerRuntimePort):
    def __init__(self, wrapped: PlannerRuntimePort, telemetry: TelemetryService, trace_context: TraceContext) -> None:
        self._wrapped = wrapped
        self._telemetry = telemetry
        self._trace = trace_context

    def run_session(
        self,
        prompt: str,
        tools: list[PlannerTool],
        max_turns: int = 15,
        session_callback: Optional[callable] = None,
    ) -> PlannerOutput:
        span = self._telemetry.start_span(self._trace)
        self._telemetry.emit(
            "llm.request",
            span,
            payload={"model": getattr(self._wrapped, "_model", "unknown"), "prompt_hash": _sha256(prompt)},
            metadata={"tool_count": len(tools), "max_turns": max_turns},
        )
        start = time.monotonic()
        try:
            output = self._wrapped.run_session(prompt, tools, max_turns=max_turns, session_callback=session_callback)
            self._telemetry.emit(
                "llm.response",
                span,
                payload={
                    "model": getattr(self._wrapped, "_model", "unknown"),
                    "latency_ms": int((time.monotonic() - start) * 1000),
                    "success": True,
                    "token_usage": {},
                },
            )
            return output
        except PlannerRuntimeError as exc:
            self._telemetry.emit(
                "llm.error",
                span,
                payload={"model": getattr(self._wrapped, "_model", "unknown"), "success": False},
                metadata={"error_type": type(exc).__name__, "message": str(exc)},
            )
            raise
