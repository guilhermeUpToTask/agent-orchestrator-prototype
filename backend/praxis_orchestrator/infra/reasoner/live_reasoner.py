"""A `Reasoner` that resolves the configured one on every call.

`reasoner.mode`, `provider_id`, `model_id`, `temperature` and `max_turns` live in
the config store precisely so an operator can change them without editing code —
but the worker builds its `PlanningHandler` once at boot and holds whatever
instance it was given, so every one of those keys was effectively boot-time only.
A `PUT /api/config/orchestrator/reasoner.*` returned success, `GET
/api/reasoner/status` reported the new value (the API process builds its own),
and the worker kept using what it started with. A successful write that does
nothing is worse than a rejected one.

Uncaching `AppContainer.reasoner` alone does not fix it: the stale reference is
the one the handler captured, not the one the property returns. The fix has to
live where the call happens, so this decorator implements the port and resolves
per call — the composition root wires it, and no handler learns about it.

**Per call, not per tick.** The four transforms here are LLM round trips lasting
seconds to minutes; resolving costs a couple of SQLite reads and one Fernet
decrypt. That granularity is also the correct one for safety: a planning call is
a whole session, so a config change lands *between* sessions and never swaps a
model out from under a conversation mid-turn.

Resolution failures now surface at the call rather than at boot, which is the
better place for them: `PlanningHandler._handle_reasoner_failure` records the
failure against the plan, where an operator can see it, instead of it being a
startup traceback in a log nobody is reading.
"""

from __future__ import annotations

from typing import Callable, Sequence

from praxis_orchestrator.domain.aggregates.planner_orchestrator import Plan
from praxis_orchestrator.domain.entities.capability import Capability
from praxis_orchestrator.domain.entities.execution_contracts import GoalContract
from praxis_orchestrator.domain.entities.goal import Goal
from praxis_orchestrator.domain.entities.task import Task
from praxis_orchestrator.domain.entities.planning_artifacts import GoalOutline
from praxis_orchestrator.domain.ports.reasoner_port import (
    ChatMessage,
    ConversationMode,
    Reasoner,
    ReasonerReply,
)


class LiveReasoner:
    """Implements `Reasoner` by delegating each call to a freshly resolved one."""

    def __init__(self, resolve: Callable[[], Reasoner]) -> None:
        #: Deliberately not called here — construction must stay free, so a
        #: worker whose secret store is misconfigured still boots and reports
        #: it, rather than dying before it can.
        self._resolve = resolve

    async def converse(
        self,
        plan: Plan,
        history: Sequence[ChatMessage],
        message: str,
        mode: ConversationMode,
    ) -> ReasonerReply:
        return await self._resolve().converse(plan, history, message, mode)

    async def enrich_goal(
        self,
        plan: Plan,
        goal: Goal,
        capabilities: Sequence[Capability],
    ) -> list[Task]:
        return await self._resolve().enrich_goal(plan, goal, capabilities)

    async def architect_cycle(self, plan: Plan) -> list[GoalOutline]:
        return await self._resolve().architect_cycle(plan)

    async def enrich_goal_contract(
        self,
        plan: Plan,
        goal: Goal,
        capabilities: Sequence[Capability],
    ) -> GoalContract:
        return await self._resolve().enrich_goal_contract(plan, goal, capabilities)
