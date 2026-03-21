"""
src/app/orchestrator.py — TaskGraphOrchestrator.

The long-running process that coordinates goal execution.

Architecture:
  - Runs as its own OS process (started by `orchestrate run <goal_id>`)
  - Subscribes to the same Redis Streams used by the task layer
    (consumer group: "goal-orchestrator")
  - Reacts to task lifecycle events and delegates to use cases

Event routing:
  task.assigned  → transition goal PENDING → RUNNING (first task dispatch)
  task.completed → GoalMergeTaskUseCase (merge branch, update goal state)
  task.canceled  → GoalCancelTaskUseCase (fail goal)

The orchestrator does not touch task assignment, execution, retries, or
reconciliation — those remain the exclusive responsibility of the task layer.
It only coordinates the goal-level view: branch merges and aggregate state.

ProjectSpec integration:
  The orchestrator loads the ProjectSpec at startup via LoadProjectSpec and
  exposes it through the read-only ``spec`` property.  Downstream consumers
  (planner, validator, reconciler) receive it via constructor injection so
  they never load it themselves.  Agents can never write the spec — only the
  approved ProposeSpecChange -> operator-apply flow may persist changes.

Design note on consumer group isolation:
  The task manager and workers use consumer groups like "task-manager" and
  "workers". The orchestrator uses "goal-orchestrator" — a separate group on
  the same streams. Redis Streams fan-out semantics guarantee that each group
  gets its own independent cursor through the event log. The orchestrator
  therefore sees every task event independently of the task manager.
"""
from __future__ import annotations

import signal

import structlog

from src.domain import DomainEvent, EventPort, GoalStatus
from src.domain.repositories import TaskRepositoryPort
from src.domain.repositories.goal_repository import GoalRepositoryPort
from src.domain.project_spec import ProjectSpec, ProjectSpecRepository
from src.app.usecases.goal_merge_task import GoalMergeTaskUseCase
from src.app.usecases.goal_cancel_task import GoalCancelTaskUseCase
from src.app.usecases.load_project_spec import LoadProjectSpec

log = structlog.get_logger(__name__)

CONSUMER_GROUP = "goal-orchestrator"
CONSUMER_NAME  = "orchestrator-0"

WATCHED_EVENTS = [
    "task.assigned",
    "task.completed",
    "task.canceled",
]


class TaskGraphOrchestrator:
    """
    Event-driven goal coordinator.

    Lifecycle:
      - run_forever() subscribes to task events and dispatches to use cases
      - shutdown() gracefully stops the loop on SIGTERM/SIGINT

    ProjectSpec:
      Pass spec_repo so the orchestrator can load the spec at startup.
      The loaded spec is injected into all downstream consumers; it is
      never reloaded mid-run unless explicitly requested by the operator.
    """

    def __init__(
        self,
        task_repo: TaskRepositoryPort,
        goal_repo: GoalRepositoryPort,
        event_port: EventPort,
        merge_usecase: GoalMergeTaskUseCase,
        cancel_usecase: GoalCancelTaskUseCase,
        spec_repo: ProjectSpecRepository | None = None,
        project_name: str = "",
    ) -> None:
        self._task_repo     = task_repo
        self._goal_repo     = goal_repo
        self._events        = event_port
        self._merge         = merge_usecase
        self._cancel        = cancel_usecase
        self._spec_repo     = spec_repo        # None → spec not loaded (dry-run / tests)
        self._project_name  = project_name
        self._running       = False
        self._spec: ProjectSpec | None = None

    # ------------------------------------------------------------------
    # ProjectSpec access
    # ------------------------------------------------------------------

    @property
    def spec(self) -> ProjectSpec:
        """
        The active ProjectSpec for this project.

        Raises RuntimeError if accessed before run_forever() has been called
        (i.e. before _load_spec() has run).
        """
        if self._spec is None:
            raise RuntimeError(
                "ProjectSpec has not been loaded yet. "
                "Call run_forever() or _load_spec() first."
            )
        return self._spec

    def _load_spec(self) -> None:
        """
        Load and cache the ProjectSpec from the repository.

        Called once at run_forever() startup. When no spec_repo is injected
        (e.g. in tests or dry-run mode) the spec is skipped with a warning —
        the orchestrator still runs, but spec-aware validation is unavailable.
        """
        if self._spec_repo is None:
            log.warning(
                "orchestrator.spec_not_configured",
                hint="Inject spec_repo to enable spec-aware validation.",
            )
            return

        load_uc = LoadProjectSpec(self._spec_repo)
        self._spec = load_uc.execute(self._project_name)
        log.info(
            "orchestrator.spec_loaded",
            project=self._project_name,
            version=self._spec.meta.version,
            domain=self._spec.objective.domain,
        )

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def run_forever(self) -> None:
        """
        Load the ProjectSpec, then subscribe to task events indefinitely.

        Handles all goals currently managed by this orchestrator instance.
        Blocks until shutdown() is called or the process receives SIGTERM.
        """
        self._install_signal_handlers()

        # spec must be loaded before any event processing begins
        self._load_spec()

        self._running = True
        log.info(
            "orchestrator.starting",
            group=CONSUMER_GROUP,
            consumer=CONSUMER_NAME,
            events=WATCHED_EVENTS,
            spec_version=self._spec.meta.version if self._spec else "none",
        )

        try:
            for event in self._events.subscribe_many(
                WATCHED_EVENTS, CONSUMER_GROUP, CONSUMER_NAME
            ):
                if not self._running:
                    break
                self._dispatch(event)
        except Exception as exc:
            log.exception("orchestrator.fatal_error", error=str(exc))
            raise
        finally:
            log.info("orchestrator.stopped")

    def shutdown(self) -> None:
        """Signal the run loop to stop after the current event is processed."""
        log.info("orchestrator.shutdown_requested")
        self._running = False

    # ------------------------------------------------------------------
    # Event dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, event: DomainEvent) -> None:
        try:
            if event.type == "task.assigned":
                self._on_task_assigned(event)
            elif event.type == "task.completed":
                self._on_task_completed(event)
            elif event.type == "task.canceled":
                self._on_task_canceled(event)
        except Exception as exc:
            log.exception(
                "orchestrator.dispatch_error",
                event_type=event.type,
                payload=event.payload,
                error=str(exc),
            )
            # Do not re-raise — a single bad event must not stop the loop.

    def _on_task_assigned(self, event: DomainEvent) -> None:
        """
        Transition the owning goal from PENDING -> RUNNING on first assignment.
        Uses the same CAS-retry pattern as the use cases so a concurrent
        version bump does not silently leave the goal stuck in PENDING.
        """
        task_id = event.payload.get("task_id")
        if not task_id:
            return

        try:
            task = self._task_repo.load(task_id)
        except KeyError:
            log.warning("orchestrator.task_not_found", task_id=task_id)
            return

        goal_id = task.feature_id
        if not goal_id:
            return

        for attempt in range(5):
            goal = self._goal_repo.get(goal_id)
            if goal is None or goal.status != GoalStatus.PENDING:
                return
            expected_v = goal.state_version
            goal.start()
            if self._goal_repo.update_if_version(goal_id, goal, expected_v):
                log.info("orchestrator.goal_started", goal_id=goal_id)
                return
            log.warning(
                "orchestrator.goal_start_cas_conflict",
                goal_id=goal_id,
                attempt=attempt,
            )
        log.error("orchestrator.goal_start_cas_exhausted", goal_id=goal_id)

    def _on_task_completed(self, event: DomainEvent) -> None:
        task_id = event.payload.get("task_id")
        if not task_id:
            return
        log.info("orchestrator.task_completed", task_id=task_id)
        self._merge.execute(task_id)

    def _on_task_canceled(self, event: DomainEvent) -> None:
        task_id = event.payload.get("task_id")
        reason  = event.payload.get("reason", "retries exhausted")
        if not task_id:
            return
        log.info("orchestrator.task_canceled", task_id=task_id, reason=reason)
        self._cancel.execute(task_id, reason)

    # ------------------------------------------------------------------
    # Signal handling
    # ------------------------------------------------------------------

    def _install_signal_handlers(self) -> None:
        def _handler(sig, _frame):
            log.info("orchestrator.signal_received", signal=sig)
            self.shutdown()

        signal.signal(signal.SIGTERM, _handler)
        signal.signal(signal.SIGINT,  _handler)
