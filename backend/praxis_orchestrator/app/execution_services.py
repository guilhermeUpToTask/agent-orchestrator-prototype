"""`ExecutionServices` — the collaborators an execution drive needs, as one value.

P8.7 task 4. `drive_goal` took **20 parameters, 11 of them optional
collaborators**, and every caller re-listed them by hand. That is not a
readability complaint: the parameters are optional, so a caller that forgets one
still type-checks, still runs, and silently loses whatever that collaborator
powered. It had already happened twice by the time this was written —

- `infra/worker/main.py` never passed `environment`/`environment_context`, so
  the P8.2/P8.5 acceptance run never fired in production (fixed in its own
  commit; `test_worker_pool.py` locks it);
- `PlanDispatcher` never passed `routing`, so the plan path silently used the
  default routing policy rather than the configured one.

Both are omissions of exactly one argument in exactly one place. Bundling turns
them into a compile-time-shaped problem: there is ONE construction site per
process, `AppContainer.execution_services`, and a new collaborator added there
reaches every driver at once.

Frozen and shared: these are long-lived adapters, not per-run state. Everything
that IS per-run — plan id, goal id, worker id, the UnitOfWork — stays an
argument, because a UoW is not thread-safe and each goal worker owns its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from praxis_orchestrator.domain.repositories.agent_repo import AgentRepository
from praxis_orchestrator.domain.repositories.model_provider_repo import ModelProviderRepository

from praxis_orchestrator.app.environment_port import EnvironmentSpec, ProjectEnvironment
from praxis_orchestrator.app.ports import (
    AgentEventSink,
    AgentRunner,
    Clock,
    PlanningArtifactStore,
    RepositoryReader,
    VerificationExecutor,
    Workspace,
)
from praxis_orchestrator.app.provider_capacity import ProviderCapacityPolicy, RoutingPolicy


@dataclass(frozen=True)
class ExecutionServices:
    """Adapters and policies one plan- or goal-drive runs against.

    The five required fields are the ones without a sane default: there is no
    such thing as executing a task with no runner, no agent catalog, no
    workspace, no event sink and no clock. Everything below them degrades to a
    documented null behaviour when absent, which is why they are the ones that
    used to go missing.
    """

    runner: AgentRunner
    agents: AgentRepository
    workspace: Workspace
    event_sink: AgentEventSink
    clock: Clock

    verifier: VerificationExecutor | None = None
    capacity: ProviderCapacityPolicy | None = None
    providers: ModelProviderRepository | None = None
    routing: RoutingPolicy | None = None
    repository_reader: RepositoryReader | None = None
    planning_artifacts: PlanningArtifactStore | None = None
    environment: ProjectEnvironment | None = None
    # (repository path, how to boot it) for one plan. A callable rather than a
    # Workspace method: `Workspace` is a FROZEN domain port, and resolving a
    # project's checkout is a composition-root job.
    environment_context: Callable[[str], tuple[Path, EnvironmentSpec | None]] | None = None
