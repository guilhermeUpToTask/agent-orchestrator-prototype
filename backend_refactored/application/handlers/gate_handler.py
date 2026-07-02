"""GateHandler — owns the human-gate phases (AWAITING_REVIEW, REVIEW) and any
non-executing phase that should pause the worker.

A gate pauses execution until a human acts (approve / edit / resume). The handler
returns PAUSED when the plan should wait, CONTINUE otherwise. It performs no LLM
work and no task execution — it only decides whether the worker holds or releases.
"""
from __future__ import annotations

from domain.aggregates.planner_orchestrator import Plan

from application.handlers.base import Signal
from application.ports import UnitOfWork


class GateHandler:
    async def handle(self, plan_id: str, plan: Plan, uow: UnitOfWork) -> Signal:
        # A plan sitting at a gate pauses the worker (it will be resumed by a human
        # action via the control use case, which frees it to be claimed again).
        return Signal.PAUSED if plan.should_pause() else Signal.CONTINUE
