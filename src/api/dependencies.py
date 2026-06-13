"""
src/api/dependencies.py — FastAPI dependency providers.

All ``get_*`` functions return use case instances via the AppContainer
singleton.  Route handlers import only from this module — never from the
container or infra layer directly.

Usage in a router:
    from typing import Annotated
    from fastapi import Depends
    from src.api.dependencies import get_plan_orchestrator

    @router.post("/approve-brief")
    def approve_brief(
        orchestrator: Annotated[PlannerOrchestrator, Depends(get_plan_orchestrator)]
    ) -> ApproveBriefResponse:
        ...
"""
from __future__ import annotations

from typing import Annotated, Callable

from fastapi import Depends

# The container is resolved through a provider so the active project context
# can be re-evaluated per request; routers never import the container directly.
_container_provider: Callable[[], object] | None = None


def _get_container():
    """Resolve the AppContainer for this request (provider bound in create_app)."""
    if _container_provider is None:
        raise RuntimeError(
            "AppContainer has not been initialised. "
            "Call create_app() before handling requests."
        )
    return _container_provider()


def set_container(container) -> None:
    """Bind a fixed container (used by tests and explicit injection)."""
    set_container_provider(lambda: container)


def set_container_provider(provider: Callable[[], object]) -> None:
    """Bind a callable that resolves the active container per request."""
    global _container_provider
    _container_provider = provider


# ── Plan / Planner ────────────────────────────────────────────────────────────

def get_plan_orchestrator(c=Depends(_get_container)):
    """PlannerOrchestrator — drives approve-brief, approve-architecture, approve-phase, discovery."""
    return c.planner_orchestrator


def get_project_plan_repo(c=Depends(_get_container)):
    return c.project_plan_repo


# ── Goals ─────────────────────────────────────────────────────────────────────

def get_goal_repo(c=Depends(_get_container)):
    return c.goal_repo


def get_goal_finalize_usecase(c=Depends(_get_container)):
    return c.goal_finalize_usecase


def get_goal_init_usecase(c=Depends(_get_container)):
    return c.goal_init_usecase


def get_unblock_goals_usecase(c=Depends(_get_container)):
    return c.unblock_goals_usecase


def get_create_goal_pr_usecase(c=Depends(_get_container)):
    return c.create_goal_pr_usecase


def get_sync_goal_pr_usecase(c=Depends(_get_container)):
    return c.sync_goal_pr_usecase


def get_advance_goal_from_pr_usecase(c=Depends(_get_container)):
    return c.advance_goal_from_pr_usecase


# ── Tasks ─────────────────────────────────────────────────────────────────────

def get_task_repo(c=Depends(_get_container)):
    return c.task_repo


def get_task_retry_usecase(c=Depends(_get_container)):
    return c.task_retry_usecase


def get_task_delete_usecase(c=Depends(_get_container)):
    return c.task_delete_usecase


def get_task_prune_usecase(c=Depends(_get_container)):
    return c.task_prune_usecase


def get_task_assign_usecase(c=Depends(_get_container)):
    return c.task_assign_usecase


def get_task_unblock_usecase(c=Depends(_get_container)):
    return c.task_unblock_usecase


def get_task_fail_handling_usecase(c=Depends(_get_container)):
    return c.task_fail_handling_usecase


# ── Agents ────────────────────────────────────────────────────────────────────

def get_agent_registry(c=Depends(_get_container)):
    return c.agent_registry


def get_agent_register_usecase(c=Depends(_get_container)):
    return c.agent_register_usecase


# ── Refinement ────────────────────────────────────────────────────────────────

def get_run_refinement_usecase(c=Depends(_get_container)):
    return c.run_refinement_usecase


# ── Spec ──────────────────────────────────────────────────────────────────────

def get_load_project_spec_usecase(c=Depends(_get_container)):
    return c.load_project_spec_usecase


def get_current_spec(c=Depends(_get_container)):
    """Return the cached active ProjectSpec aggregate."""
    return c.current_spec


def get_project_name(c=Depends(_get_container)) -> str:
    """Return the configured project name (raises ConfigurationError if unset)."""
    return c.get_required_project()


def get_propose_spec_change_usecase(c=Depends(_get_container)):
    return c.propose_spec_change_usecase


def get_validate_against_spec_usecase(c=Depends(_get_container)):
    return c.validate_against_spec_usecase


# ── Project ───────────────────────────────────────────────────────────────────

def get_project_reset_usecase(c=Depends(_get_container)):
    return c.project_reset_usecase


def get_settings_context(c=Depends(_get_container)):
    """Return the active SettingsContext (machine + project + secrets)."""
    return c.ctx


# ── Annotated shorthands (modern DI style) ────────────────────────────────────
# Import these in route handlers for the cleanest signatures:
#
#   def my_route(uc: PlanOrchestratorDep) -> ...:

PlanOrchestratorDep = Annotated[object, Depends(get_plan_orchestrator)]
ProjectPlanRepoDep = Annotated[object, Depends(get_project_plan_repo)]

GoalRepoDep = Annotated[object, Depends(get_goal_repo)]
GoalFinalizeUseCaseDep = Annotated[object, Depends(get_goal_finalize_usecase)]
GoalInitUseCaseDep = Annotated[object, Depends(get_goal_init_usecase)]
UnblockGoalsUseCaseDep = Annotated[object, Depends(get_unblock_goals_usecase)]
CreateGoalPRUseCaseDep = Annotated[object, Depends(get_create_goal_pr_usecase)]
SyncGoalPRUseCaseDep = Annotated[object, Depends(get_sync_goal_pr_usecase)]
AdvanceGoalFromPRUseCaseDep = Annotated[object, Depends(get_advance_goal_from_pr_usecase)]

TaskRepoDep = Annotated[object, Depends(get_task_repo)]
TaskRetryUseCaseDep = Annotated[object, Depends(get_task_retry_usecase)]
TaskDeleteUseCaseDep = Annotated[object, Depends(get_task_delete_usecase)]
TaskPruneUseCaseDep = Annotated[object, Depends(get_task_prune_usecase)]
TaskAssignUseCaseDep = Annotated[object, Depends(get_task_assign_usecase)]
TaskUnblockUseCaseDep = Annotated[object, Depends(get_task_unblock_usecase)]
TaskFailHandlingUseCaseDep = Annotated[object, Depends(get_task_fail_handling_usecase)]

AgentRegistryDep = Annotated[object, Depends(get_agent_registry)]
AgentRegisterUseCaseDep = Annotated[object, Depends(get_agent_register_usecase)]

RunRefinementUseCaseDep = Annotated[object, Depends(get_run_refinement_usecase)]

LoadProjectSpecUseCaseDep = Annotated[object, Depends(get_load_project_spec_usecase)]
CurrentSpecDep = Annotated[object, Depends(get_current_spec)]
ProjectNameDep = Annotated[str, Depends(get_project_name)]
ProposeSpecChangeUseCaseDep = Annotated[object, Depends(get_propose_spec_change_usecase)]
ValidateAgainstSpecUseCaseDep = Annotated[object, Depends(get_validate_against_spec_usecase)]

ProjectResetUseCaseDep = Annotated[object, Depends(get_project_reset_usecase)]
SettingsContextDep = Annotated[object, Depends(get_settings_context)]
