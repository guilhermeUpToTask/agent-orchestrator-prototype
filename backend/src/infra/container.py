"""AppContainer — the composition root (rebuilt during the integration).

TRANSPLANT NOTE: the old container wired the pre-refactor domain (task manager /
goal orchestrator / reconciler daemons) and was emptied with them. It grows back
stage by stage as the real adapters land behind the new ports:

  Stage 3 — engine/session factory, SystemClock, SqliteUnitOfWork (plans+outbox)
  Stage 4 — reference-data repos (agents/capabilities/providers/models), secrets
  Stage 5 — workspace + agent-runner adapters, agent-event sink
  Stage 6 — reasoner, PlanDispatcher, worker wiring
  Stage 7 — API dependency surface
"""
from __future__ import annotations


class AppContainer:
    """Lazy composition root. Adapters are added as integration stages land."""
