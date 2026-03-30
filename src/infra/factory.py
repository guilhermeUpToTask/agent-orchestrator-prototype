"""
src/infra/factory.py — Thin backward-compatibility shim.

All real dependency wiring now lives in AppContainer (src/infra/container.py).
These functions delegate to a container instance built from the environment.

New code should use AppContainer directly:
    app = AppContainer.from_env()
    usecase = app.goal_init_usecase

These functions exist only for code that hasn't been migrated yet and for
external callers (e.g. tests) that depend on the old interface.
"""
from __future__ import annotations
from src.infra.container import AppContainer
from src.infra.settings import SettingsContext


def _app(ctx: SettingsContext | None = None) -> AppContainer:
    if ctx is not None:
        return AppContainer(ctx)
    return AppContainer.from_env()


# ── paths / settings ───────────────────────────────────────────────────────
def build_project_paths(ctx=None):       return _app(ctx).paths
def build_project_settings(ctx=None):    return _app(ctx).ctx.project

# ── repositories ───────────────────────────────────────────────────────────
def build_task_repo(ctx=None):           return _app(ctx).task_repo
def build_goal_repo(ctx=None):           return _app(ctx).goal_repo
def build_agent_registry(ctx=None):      return _app(ctx).agent_registry
def build_spec_repo(ctx=None):           return _app(ctx).spec_repo
def build_project_plan_repo(ctx=None):   return _app(ctx).project_plan_repo
def build_planner_session_repo(ctx=None):return _app(ctx).planner_session_repo

# ── ports ──────────────────────────────────────────────────────────────────
def build_event_port(ctx=None):          return _app(ctx).event_port
def build_lease_port(ctx=None):          return _app(ctx).lease_port
def build_telemetry_emitter(ctx=None):   return _app(ctx).telemetry_emitter
def build_git_workspace(ctx=None):       return _app(ctx).git_workspace
def build_github_client(ctx=None):       return _app(ctx).github_client

# ── runtimes ───────────────────────────────────────────────────────────────
def build_runtime_factory():             return _app().runtime_factory
def build_planner_runtime(ctx=None):     return _app(ctx).planner_runtime
def build_interactive_planner_runtime(io_handler=None, ctx=None): return _app(ctx).interactive_planner_runtime

def build_agent_runtime(agent_props):
    from src.infra.runtime.factory import build_agent_runtime as _build
    return _build(agent_props)

def build_lease_refresher_factory():     return _app().lease_refresher_factory

# ── use cases ──────────────────────────────────────────────────────────────
def build_task_creation_service(ctx=None):      return _app(ctx).task_creation_service
def build_task_manager_handler(ctx=None):       return _app(ctx).task_manager_handler
def build_worker_handler(ctx=None):             return _app(ctx).worker_handler
def build_task_execute_usecase(ctx=None):       return _app(ctx).task_execute_usecase
def build_task_retry_usecase(ctx=None):         return _app(ctx).task_retry_usecase
def build_task_delete_usecase(ctx=None):        return _app(ctx).task_delete_usecase
def build_task_prune_usecase(ctx=None):         return _app(ctx).task_prune_usecase
def build_task_assign_usecase(ctx=None):        return _app(ctx).task_assign_usecase
def build_task_fail_handling_usecase(ctx=None): return _app(ctx).task_fail_handling_usecase
def build_task_unblock_usecase(ctx=None):       return _app(ctx).task_unblock_usecase
def build_agent_register_usecase(ctx=None):     return _app(ctx).agent_register_usecase
def build_project_reset_usecase(ctx=None):      return _app(ctx).project_reset_usecase
def build_goal_init_usecase(ctx=None):          return _app(ctx).goal_init_usecase
def build_goal_merge_task_usecase(ctx=None):    return _app(ctx).goal_merge_task_usecase
def build_goal_cancel_task_usecase(ctx=None):   return _app(ctx).goal_cancel_task_usecase
def build_goal_finalize_usecase(ctx=None):      return _app(ctx).goal_finalize_usecase
def build_unblock_goals_usecase(ctx=None):      return _app(ctx).unblock_goals_usecase
def build_advance_goal_from_pr_usecase(ctx=None): return _app(ctx).advance_goal_from_pr_usecase
def build_load_project_spec(ctx=None):          return _app(ctx).load_project_spec_usecase
def build_validate_against_spec(ctx=None):      return _app(ctx).validate_against_spec_usecase
def build_propose_spec_change(ctx=None):        return _app(ctx).propose_spec_change_usecase
def build_project_state_adapter(ctx=None):      return _app(ctx).project_state
def build_task_graph_orchestrator(ctx=None):    return _app(ctx).task_graph_orchestrator
def build_task_graph_orchestrator_with_pr(ctx=None): return _app(ctx).task_graph_orchestrator_with_pr
def build_reconciler(interval_seconds=60, stuck_task_min_age_seconds=120, ctx=None):
    return _app(ctx).reconciler
def build_planner_orchestrator(io_handler=None, ctx=None): return _app(ctx).planner_orchestrator
def build_planner_context_assembler(ctx=None):  return _app(ctx).planner_context_assembler

def build_create_goal_pr_usecase(base_branch: str = "main", ctx=None):
    return _app(ctx).create_goal_pr_usecase

def build_sync_goal_pr_usecase(ctx=None):       return _app(ctx).sync_goal_pr_usecase
