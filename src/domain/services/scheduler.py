"""
src/domain/services/scheduler.py — Agent scheduling service.
"""
from __future__ import annotations

from typing import Optional

from src.domain.aggregates.task import TaskAggregate
from src.domain.entities.agent import AgentProps


class SchedulerService:
    """
    Selects the best available agent for a task.

    Eligibility and scoring are fully delegated to AgentProps so this
    service is pure coordination: filter candidates then rank them.
    It holds no state and has no infrastructure dependencies.
    """

    def select_agent(
        self,
        task: TaskAggregate,
        agents: list[AgentProps],
    ) -> Optional[AgentProps]:
        """
        Return the highest-scoring eligible agent, or None if no match.

        Eligibility: AgentProps.matches_selector()
        Scoring:     AgentProps.scheduling_score()
        """
        candidates = [a for a in agents if a.matches_selector(task.agent_selector)]
        if not candidates:
            return None
        return max(candidates, key=lambda a: a.scheduling_score())

    def eligible_agents(
        self,
        task: TaskAggregate,
        agents: list[AgentProps],
    ) -> list[AgentProps]:
        """Return all eligible agents without ranking."""
        return [a for a in agents if a.matches_selector(task.agent_selector)]
