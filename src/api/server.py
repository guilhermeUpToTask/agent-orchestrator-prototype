"""
src/api/server.py — AIPOM FastAPI server.
"""
from __future__ import annotations

import asyncio
import json
import queue
from typing import Any, Optional

import structlog
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

log = structlog.get_logger(__name__)


class ApproveBriefResponse(BaseModel):
    plan_status: str
    vision: str


class ApproveArchitectureRequest(BaseModel):
    decision_ids: list[str]


class ApproveArchitectureResponse(BaseModel):
    decisions_applied: int
    goals_dispatched: list[str]
    plan_status: str


class ApprovePhaseRequest(BaseModel):
    approve_next: bool = True


class ApprovePhaseResponse(BaseModel):
    decisions_applied: int
    goals_dispatched: list[str]
    plan_status: str


class RefineRequest(BaseModel):
    message: str
    focused_node_id: Optional[str] = None
    focused_goal_id: Optional[str] = None


class RefineResponse(BaseModel):
    session_id: str
    actions_taken: list[str]
    succeeded: bool
    error: Optional[str] = None


class DiscoveryMessageRequest(BaseModel):
    message: str


class DiscoveryMessageResponse(BaseModel):
    question: Optional[str]
    done: bool
    brief: Optional[dict] = None


_sse_queue: queue.Queue[dict] = queue.Queue(maxsize=200)


def publish_sse(event_type: str, payload: dict) -> None:
    try:
        _sse_queue.put_nowait({"type": event_type, "payload": payload})
    except queue.Full:
        log.warning("api.sse_queue_full", event_type=event_type)


def create_app(container=None) -> FastAPI:
    if container is None:
        from src.infra.container import AppContainer

        container = AppContainer.from_env()

    app = FastAPI(title="AIPOM Planning API", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _planner_hook(event_type: str, data: dict) -> None:
        publish_sse(f"plan.{event_type}", data)

    try:
        container.planner_orchestrator.set_planner_event_hook(_planner_hook)
    except Exception as exc:
        log.warning("api.planner_hook_setup_failed", error=str(exc))

    def get_container():
        return container

    @app.get("/api/plan")
    def get_plan(c=Depends(get_container)) -> dict:
        try:
            plan = c.project_plan_repo.load()
        except Exception:
            return {
                "status": "discovery",
                "vision": "",
                "phases": [],
                "current_phase_index": 0,
                "plan_id": None,
            }
        return _plan_to_dict(plan)

    @app.get("/api/goals")
    def get_goals(c=Depends(get_container)) -> list[dict]:
        goals = c.goal_repo.list_all()
        return [_goal_to_dict(g) for g in goals]

    @app.get("/api/agents")
    def get_agents(c=Depends(get_container)) -> list[dict]:
        agents = c.agent_registry.list_agents()
        return [_agent_to_dict(a) for a in agents]

    @app.get("/api/plan/history")
    def get_plan_history(c=Depends(get_container)) -> list[dict]:
        try:
            plan = c.project_plan_repo.load()
            return [h.model_dump() for h in plan.history]
        except Exception:
            return []

    @app.get("/api/goals/{goal_id}/history")
    def get_goal_history(goal_id: str, c=Depends(get_container)) -> list[dict]:
        try:
            goal = c.goal_repo.load(goal_id)
            return [h.model_dump() for h in goal.history]
        except Exception:
            raise HTTPException(status_code=404, detail=f"Goal {goal_id} not found")

    @app.post("/api/plan/approve-brief", response_model=ApproveBriefResponse)
    def approve_brief(c=Depends(get_container)) -> ApproveBriefResponse:
        try:
            plan = c.planner_orchestrator.approve_brief()
            publish_sse("plan.status_changed", {"status": plan.status.value})
            return ApproveBriefResponse(plan_status=plan.status.value, vision=plan.vision)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @app.post("/api/plan/approve-architecture", response_model=ApproveArchitectureResponse)
    def approve_architecture(
        body: ApproveArchitectureRequest,
        c=Depends(get_container),
    ) -> ApproveArchitectureResponse:
        try:
            result = c.planner_orchestrator.approve_architecture(decision_ids=body.decision_ids)
            publish_sse("plan.status_changed", {"status": result.plan_status})
            for goal_id in result.goals_dispatched:
                publish_sse("goal.dispatched", {"goal_id": goal_id})
            return ApproveArchitectureResponse(
                decisions_applied=result.decisions_applied,
                goals_dispatched=result.goals_dispatched,
                plan_status=result.plan_status,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @app.post("/api/plan/approve-phase", response_model=ApprovePhaseResponse)
    def approve_phase(
        body: ApprovePhaseRequest,
        c=Depends(get_container),
    ) -> ApprovePhaseResponse:
        try:
            result = c.planner_orchestrator.approve_phase_review(approve_next=body.approve_next)
            publish_sse("plan.status_changed", {"status": result.plan_status})
            return ApprovePhaseResponse(
                decisions_applied=result.decisions_applied,
                goals_dispatched=result.goals_dispatched,
                plan_status=result.plan_status,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @app.post("/api/plan/refine", response_model=RefineResponse)
    def refine_plan(body: RefineRequest, c=Depends(get_container)) -> RefineResponse:
        result = c.run_refinement_usecase.execute(
            user_message=body.message,
            focused_node_id=body.focused_node_id,
            focused_goal_id=body.focused_goal_id,
        )
        for action in result.actions_taken:
            publish_sse("plan.refinement_action", {"action": action})

        return RefineResponse(
            session_id=result.session_id,
            actions_taken=result.actions_taken,
            succeeded=result.succeeded,
            error=result.error,
        )

    _discovery_question_q: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
    _discovery_answer_q: queue.Queue[str] = queue.Queue(maxsize=1)

    def _put_discovery_question(question: str) -> None:
        try:
            _discovery_question_q.put_nowait(question)
        except asyncio.QueueFull:
            log.warning("api.discovery_question_queue_full")

    @app.post("/api/plan/discovery/start")
    async def start_discovery(c=Depends(get_container)) -> dict:
        loop = asyncio.get_running_loop()

        def io_handler(question: str) -> str:
            loop.call_soon_threadsafe(_put_discovery_question, question)
            return _discovery_answer_q.get()

        future = loop.run_in_executor(
            None,
            lambda: c.planner_orchestrator.start_discovery(io_handler=io_handler),
        )

        try:
            question = await asyncio.wait_for(_discovery_question_q.get(), timeout=30.0)
            return {"question": question, "done": False}
        except asyncio.TimeoutError:
            if not future.done():
                raise HTTPException(status_code=504, detail="Discovery did not produce a question in time")
            result = await future
            return {"done": True, "brief": result.brief.model_dump() if result.brief else None}

    @app.post("/api/plan/discovery/message", response_model=DiscoveryMessageResponse)
    async def discovery_message(body: DiscoveryMessageRequest) -> DiscoveryMessageResponse:
        try:
            _discovery_answer_q.put_nowait(body.message)
        except queue.Full:
            raise HTTPException(status_code=409, detail="Discovery already has a pending answer")
        try:
            question = await asyncio.wait_for(_discovery_question_q.get(), timeout=60.0)
            return DiscoveryMessageResponse(question=question, done=False)
        except asyncio.TimeoutError:
            return DiscoveryMessageResponse(question=None, done=True)

    @app.get("/api/events")
    async def event_stream(request: Request) -> StreamingResponse:
        async def generator():
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.to_thread(_sse_queue.get, True, 25.0)
                    data = json.dumps(event)
                    yield f"data: {data}\n\n"
                except queue.Empty:
                    yield ": ping\n\n"

        return StreamingResponse(
            generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


def _plan_to_dict(plan: Any) -> dict:
    return {
        "plan_id": plan.plan_id,
        "status": plan.status.value,
        "vision": plan.vision,
        "architecture_summary": plan.architecture_summary,
        "current_phase_index": plan.current_phase_index,
        "state_version": plan.state_version,
        "phases": [
            {
                "index": p.index,
                "name": p.name,
                "goal": p.goal,
                "goal_names": p.goal_names,
                "status": p.status.value,
                "exit_criteria": p.exit_criteria,
                "lessons": p.lessons,
            }
            for p in plan.phases
        ],
        "brief": (
            {
                "vision": plan.brief.vision,
                "constraints": plan.brief.constraints,
                "phase_1_exit_criteria": plan.brief.phase_1_exit_criteria,
                "open_questions": plan.brief.open_questions,
            }
            if plan.brief
            else None
        ),
        "history": [h.model_dump() for h in plan.history],
    }


def _goal_to_dict(goal: Any) -> dict:
    return {
        "goal_id": goal.goal_id,
        "name": goal.name,
        "description": goal.description,
        "status": goal.status.value,
        "feature_tag": goal.feature_tag,
        "depends_on": goal.depends_on,
        "tasks": [
            {
                "task_id": t.task_id,
                "title": t.title,
                "status": t.status.value,
                "depends_on": t.depends_on,
            }
            for t in goal.tasks.values()
        ],
        "history": [h.model_dump() for h in goal.history],
    }


def _agent_to_dict(agent: Any) -> dict:
    return {
        "agent_id": agent.agent_id,
        "name": agent.name,
        "capabilities": agent.capabilities,
        "version": agent.version,
        "trust_level": agent.trust_level.value
        if hasattr(agent.trust_level, "value")
        else agent.trust_level,
        "active": agent.active,
        "max_concurrent_tasks": agent.max_concurrent_tasks,
    }
