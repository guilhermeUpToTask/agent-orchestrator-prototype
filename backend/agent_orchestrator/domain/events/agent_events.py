"""Fine-grained agent runtime events — tool calls, steps, tokens streamed by the
pi runner during a task run. BEST-EFFORT telemetry, NOT transactional with state
(they stream mid-run, between the two state transactions). Tagged by attempt so a
re-run after a crash is distinguishable in the live view."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class AgentEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    plan_id: str
    goal_id: str | None = None
    # NULL task_id = a plan-scoped telemetry row (e.g. the reasoner's llm.call);
    # task-scoped runtime events carry the task id.
    task_id: str | None = None
    run_id: str | None = None
    attempt_id: str | None = None
    attempt: int
    seq: int  # per-(task,attempt) ordering; not a global guarantee
    type: str  # e.g. "tool_call", "step", "token", "llm.call"
    payload: dict[str, Any] = Field(default_factory=dict)
