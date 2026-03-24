from __future__ import annotations

import hashlib
from typing import Optional

from src.app.telemetry.service import TelemetryService
from src.app.telemetry.tracing import TraceContext
from src.domain.ports.project_state import DecisionEntry, ProjectStatePort


def _hash(v: str | None) -> str | None:
    if v is None:
        return None
    return hashlib.sha256(v.encode("utf-8")).hexdigest()


class TelemetryProjectStateAdapter(ProjectStatePort):
    def __init__(self, wrapped: ProjectStatePort, telemetry: TelemetryService, trace_context: TraceContext) -> None:
        self._wrapped = wrapped
        self._telemetry = telemetry
        self._trace = trace_context

    def read_state(self, key: str) -> Optional[str]:
        return self._wrapped.read_state(key)

    def write_state(self, key: str, content: str) -> None:
        before = self._wrapped.read_state(key)
        self._wrapped.write_state(key, content)
        span = self._telemetry.start_span(self._trace)
        self._telemetry.emit(
            "state.updated",
            span,
            payload={"key": key, "old_hash": _hash(before), "new_hash": _hash(content)},
            metadata={"old_len": len(before or ""), "new_len": len(content)},
        )

    def list_keys(self) -> list[str]:
        return self._wrapped.list_keys()

    def delete_state(self, key: str) -> bool:
        ok = self._wrapped.delete_state(key)
        if ok:
            span = self._telemetry.start_span(self._trace)
            self._telemetry.emit("state.updated", span, payload={"key": key, "operation": "delete"})
        return ok

    def write_decision(self, entry: DecisionEntry) -> None:
        self._wrapped.write_decision(entry)
        span = self._telemetry.start_span(self._trace)
        self._telemetry.emit(
            "state.updated",
            span,
            payload={"key": f"decision:{entry.id}", "operation": "write_decision"},
            metadata={"domain": entry.domain, "status": entry.status},
        )

    def list_decisions(self, domain: Optional[str] = None, status: str = "active") -> list[DecisionEntry]:
        return self._wrapped.list_decisions(domain=domain, status=status)

    def supersede_decision(self, id: str, superseded_by: str, reason: str) -> bool:
        ok = self._wrapped.supersede_decision(id=id, superseded_by=superseded_by, reason=reason)
        if ok:
            span = self._telemetry.start_span(self._trace)
            self._telemetry.emit(
                "state.updated",
                span,
                payload={"key": f"decision:{id}", "operation": "supersede"},
                metadata={"superseded_by": superseded_by},
            )
        return ok
