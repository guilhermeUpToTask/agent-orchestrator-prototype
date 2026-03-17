"""
src/domain/entities/agent.py — AgentProps entity.

AgentProps is a snapshot of an agent's registered state. All agent-level
domain decisions live here — callers never inspect raw fields to make
eligibility or scheduling decisions.

Domain behaviours:
  is_alive()          — heartbeat freshness check
  satisfies_version() — semver constraint evaluation
  matches_selector()  — full eligibility check for a task's AgentSelector
  scheduling_score()  — comparable tuple used by SchedulerService to rank candidates
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field

from src.domain.value_objects.status import TrustLevel
from src.domain.value_objects.task import AgentSelector


# ---------------------------------------------------------------------------
# Private version helpers
# ---------------------------------------------------------------------------

def _parse_version(v: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", v)
    return tuple(int(p) for p in parts[:3])


def _satisfies_version(agent_version: str, constraint: str) -> bool:
    constraint = constraint.strip()
    if constraint.startswith(">="):
        return _parse_version(agent_version) >= _parse_version(constraint[2:])
    return _parse_version(agent_version) == _parse_version(constraint)


# ---------------------------------------------------------------------------
# AgentProps
# ---------------------------------------------------------------------------

class AgentProps(BaseModel):
    agent_id: str
    name: str
    capabilities: list[str] = Field(default_factory=list)
    version: str = "1.0.0"
    tools: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    endpoint: Optional[str] = None
    last_heartbeat: Optional[datetime] = None
    max_concurrent_tasks: int = 1
    trust_level: TrustLevel = TrustLevel.MEDIUM
    metadata: dict[str, Any] = Field(default_factory=dict)
    active: bool = True
    runtime_type: str = "gemini"
    runtime_config: dict[str, Any] = Field(default_factory=dict)

    def is_alive(self, threshold_seconds: int = 60) -> bool:
        """Return True if a heartbeat was received within threshold_seconds."""
        if self.last_heartbeat is None:
            return False
        age = (datetime.now(timezone.utc) - self.last_heartbeat).total_seconds()
        return age < threshold_seconds

    def satisfies_version(self, constraint: str) -> bool:
        """Return True if this agent's version satisfies the given semver constraint."""
        return _satisfies_version(self.version, constraint)

    def matches_selector(self, selector: AgentSelector) -> bool:
        """
        Return True if this agent is fully eligible to execute a task with
        the given selector.

        Checks: active flag + alive heartbeat + required capability + version.
        """
        return (
            self.active
            and self.is_alive()
            and selector.required_capability in self.capabilities
            and self.satisfies_version(selector.min_version)
        )

    def scheduling_score(self) -> tuple:
        """
        Comparable score for ranking eligible agents.
        Higher is better.  Precedence: trust > max_concurrent > tool count.
        """
        trust_rank = {"high": 3, "medium": 2, "low": 1}.get(self.trust_level.value, 0)
        return (trust_rank, self.max_concurrent_tasks, len(self.tools))
