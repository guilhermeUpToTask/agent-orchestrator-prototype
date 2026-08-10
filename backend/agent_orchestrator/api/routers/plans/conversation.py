"""The conversational phases: discovery and replanning messages, and the
persisted chat history. The reply travels in the HTTP response body — SSE
carries domain events only, never a dual-published reply."""

from __future__ import annotations


from fastapi import APIRouter, Depends

from agent_orchestrator.api.dependencies import get_container
from agent_orchestrator.app.use_cases.conversation import discovery_message, replanning_message
from agent_orchestrator.infra.container import AppContainer


from agent_orchestrator.api.routers.plans.schemas import (
    ChatMessageResponse,
    MessageRequest,
    MessageResponse,
)

router = APIRouter(prefix="/plans", tags=["plans"])


@router.post("/{plan_id}/discovery/message", response_model=MessageResponse)
async def discovery(
    plan_id: str,
    body: MessageRequest,
    container: AppContainer = Depends(get_container),
) -> MessageResponse:
    """One DISCOVERY conversation turn. Multi-turn: committed=false keeps the
    conversation open; committed=true is the roadmap commit -> ARCHITECTURE."""
    result = await discovery_message(
        plan_id,
        body.message,
        container.new_unit_of_work(),
        container.reasoner,
        container.chat_store,
        container.clock,
    )
    return MessageResponse(
        reply=result.reply,
        committed=result.committed,
        phase=result.phase.value,
        operation_id=result.operation_id,
        operation_status=result.operation_status.value,
        error=result.error,
    )


@router.post("/{plan_id}/replanning/message", response_model=MessageResponse)
async def replanning(
    plan_id: str,
    body: MessageRequest,
    container: AppContainer = Depends(get_container),
) -> MessageResponse:
    """One REPLANNING conversation turn. committed=true commits the new goal
    set -> ARCHITECTURE (the iteration increments here)."""
    result = await replanning_message(
        plan_id,
        body.message,
        container.new_unit_of_work(),
        container.reasoner,
        container.chat_store,
        container.clock,
    )
    return MessageResponse(
        reply=result.reply,
        committed=result.committed,
        phase=result.phase.value,
        operation_id=result.operation_id,
        operation_status=result.operation_status.value,
        error=result.error,
    )




@router.get("/{plan_id}/chat", response_model=list[ChatMessageResponse])
def chat_history(
    plan_id: str, container: AppContainer = Depends(get_container)
) -> list[ChatMessageResponse]:
    """The plan's DISCOVERY/REPLANNING conversation, in order. 404s for an
    unknown plan (chat rows only exist for real plans)."""
    uow = container.new_unit_of_work()
    with uow:
        uow.plans.get(plan_id)  # existence check -> PLAN_NOT_FOUND -> 404
    return [
        ChatMessageResponse(
            role=m.role,
            content=m.content,
            created_at=m.created_at.isoformat(),
            meta=dict(m.meta),
        )
        for m in container.chat_store.list(plan_id)
    ]
