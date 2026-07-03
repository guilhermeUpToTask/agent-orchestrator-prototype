"""conversation — the chat-driven phases (the driver model's third driver).

DISCOVERY and REPLANNING are advanced by USER MESSAGES, not by workers (the
claim predicate hides them from workers entirely). Each message is one reasoner
turn — REAL multi-turn chat: a turn that returns no goals keeps the phase and
just replies (a question, a suggestion); the turn whose reply carries a goal
set is the ROADMAP COMMIT that moves the plan into ARCHITECTURE.

  discovery_message  — DISCOVERY: brief + chat history -> converse. Commit path:
                       set_iteration_goals + advance to ARCHITECTURE.
  replanning_message — REPLANNING: prior DONE results + chat -> converse. Commit
                       path: commit_replanned_goals (the ONE defined point where
                       the iteration increments and finalize-abandon closes what
                       the abandoned iteration left non-terminal).

Choreography per turn (the crash-safety shape used everywhere):
  1. guard the phase on a fresh read;
  2. persist the USER message BEFORE the LLM call (chat store, own txn — the
     user's words survive any reasoner failure);
  3. reasoner.converse(...) OUTSIDE any transaction (LLM side effect);
  4. no goals -> append the assistant reply, phase unchanged;
     goals    -> re-open the plan txn, RE-GUARD the phase (a racing human
     command wins), write goal set + phase + PhaseAdvanced atomically, then
     append the assistant reply (meta committed=true).

Chat is display history on its own short transactions; the plan transaction is
truth — neither can roll the other back.
"""
from __future__ import annotations

from pydantic import BaseModel

from src.domain.aggregates.planner_orchestrator import PlanPhase
from src.domain.errors.tasks_errors import InvalidTransitionError
from src.domain.events.outbox import PhaseAdvanced

from src.app.ports import (
    ChatMessage,
    ChatStore,
    Clock,
    ConversationMode,
    Reasoner,
    UnitOfWork,
)


class ConversationResult(BaseModel):
    """What a message turn produced: the assistant reply to display, whether
    the roadmap was committed, and the (possibly advanced) phase."""

    reply: str
    committed: bool
    phase: PlanPhase


async def discovery_message(
    plan_id: str,
    message: str,
    uow: UnitOfWork,
    reasoner: Reasoner,
    chat: ChatStore,
    clock: Clock,
) -> ConversationResult:
    return await _conversation_turn(
        plan_id, message, uow, reasoner, chat, clock, PlanPhase.DISCOVERY
    )


async def replanning_message(
    plan_id: str,
    message: str,
    uow: UnitOfWork,
    reasoner: Reasoner,
    chat: ChatStore,
    clock: Clock,
) -> ConversationResult:
    return await _conversation_turn(
        plan_id, message, uow, reasoner, chat, clock, PlanPhase.REPLANNING
    )


async def _conversation_turn(
    plan_id: str,
    message: str,
    uow: UnitOfWork,
    reasoner: Reasoner,
    chat: ChatStore,
    clock: Clock,
    expected_phase: PlanPhase,
) -> ConversationResult:
    with uow:
        plan = uow.plans.get(plan_id)
    if plan.phase != expected_phase:
        raise InvalidTransitionError(
            "Plan", plan_id, plan.phase.value, PlanPhase.ARCHITECTURE.value
        )

    # history BEFORE this turn; then persist the user message so it survives
    # any reasoner failure (chat store = own short txn, not the plan txn)
    history = chat.list(plan_id)
    chat.append(
        plan_id,
        ChatMessage(role="user", content=message, created_at=clock.now()),
    )

    mode: ConversationMode = (
        "discovery" if expected_phase == PlanPhase.DISCOVERY else "replanning"
    )
    reply = await reasoner.converse(plan, history, message, mode)  # LLM, no txn

    if reply.goals is None:
        chat.append(
            plan_id,
            ChatMessage(
                role="assistant",
                content=reply.message,
                created_at=clock.now(),
                meta={"committed": False},
            ),
        )
        return ConversationResult(
            reply=reply.message, committed=False, phase=expected_phase
        )

    with uow:
        plan = uow.plans.get(plan_id)
        if plan.phase != expected_phase:
            raise InvalidTransitionError(
                "Plan", plan_id, plan.phase.value, PlanPhase.ARCHITECTURE.value
            )
        if expected_phase == PlanPhase.DISCOVERY:
            plan.set_iteration_goals(reply.goals)
            plan.advance_phase(PlanPhase.ARCHITECTURE)
        else:
            # guards REPLANNING itself: finalize-abandon of leftover
            # non-terminal work, append-only goal addition, iteration += 1
            plan.commit_replanned_goals(reply.goals)
        plan.bump_version()
        uow.outbox.add(
            PhaseAdvanced(
                plan_id=plan_id,
                from_phase=expected_phase.value,
                to_phase=PlanPhase.ARCHITECTURE.value,
            )
        )
        uow.plans.save(plan)

    chat.append(
        plan_id,
        ChatMessage(
            role="assistant",
            content=reply.message,
            created_at=clock.now(),
            meta={"committed": True},
        ),
    )
    return ConversationResult(
        reply=reply.message, committed=True, phase=PlanPhase.ARCHITECTURE
    )
