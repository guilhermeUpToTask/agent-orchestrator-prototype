"""Phase handlers: one concern per phase.

advance_plan is a thin DISPATCHER that routes on plan.phase to a handler. Each
handler owns exactly one concern and returns a Signal. This keeps task execution
(the pull-scan loop) separate from planning (the reasoner phases) separate from
the human gates — instead of one god-function branching on everything.

Adding a phase = adding/extending a handler, never touching the others.
"""
from __future__ import annotations

from enum import Enum
from typing import Protocol

from src.domain.aggregates.planner_orchestrator import Plan

from src.app.ports import UnitOfWork


class Signal(str, Enum):
    """What one advance step tells the worker loop to do next."""

    CONTINUE = "continue"      # made progress; loop again immediately
    NOT_READY = "not_ready"    # work remains but backing off; release & re-check later
    PAUSED = "paused"          # waiting on a human gate; release until resumed
    DONE = "done"             # plan complete (terminal)
    FAILED = "failed"          # plan halted by failure (terminal)


class PhaseHandler(Protocol):
    """Handles one advance step for the phase(s) it owns. Given the plan_id and the
    collaborators it needs, performs ONE unit of work and returns a Signal."""

    async def handle(self, plan_id: str, plan: Plan, uow: UnitOfWork) -> Signal: ...
