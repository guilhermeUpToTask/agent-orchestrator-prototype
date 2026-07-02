"""GateHandler — owns the human-gate phases (AWAITING_REVIEW, REVIEW).

A gate pauses execution until a human acts (approve / edit / finish / replan via
the control use cases). The handler returns PAUSED unconditionally: a plan sitting
at a gate is, by definition, waiting for a human — there is no "continue" case.
(The old conditional `should_pause()` check was the verified gate-spin bug: a plan
at a gate not listed in pause_after spun the worker loop to max_steps.)

Gates are also not worker-claimable (the claim predicate excludes them), so this
handler is a defensive backstop for direct advance calls, not the normal path.
"""
from __future__ import annotations

from src.domain.aggregates.planner_orchestrator import Plan

from src.app.handlers.base import Signal
from src.app.ports import UnitOfWork


class GateHandler:
    async def handle(self, plan_id: str, plan: Plan, uow: UnitOfWork) -> Signal:
        return Signal.PAUSED
