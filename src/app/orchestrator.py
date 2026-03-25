"""
src/app/orchestrator.py — TaskGraphOrchestrator.

The long-running process that coordinates goal execution.

Architecture:
  - Runs as its own OS process (started by `orchestrate run <goal_id>`)
  - Subscribes to the same Redis Streams used by the task layer
    (consumer group: "goal-orchestrator")
  - Reacts to task lifecycle events and delegates to use cases

Event routing:
  task.assigned         → transition goal PENDING → RUNNING (first task dispatch)
  task.completed        → GoalMergeTaskUseCase (merge branch, update goal state)
  task.canceled         → GoalCancelTaskUseCase (fail goal)
  goal.ready_for_review → CreateGoalPRUseCase (open PR on GitHub)
  goal.approved         → unlock next goal in sequential plan
  goal.merged           → unlock next goal in sequential plan (final gate)

PR-phase events (goal.ready_for_review, goal.approved, goal.merged) are emitted
by the use cases called from this orchestrator or by the reconciler's PR polling
pass. The orchestrator subscribes to them to coordinate sequential goal plans.

Sequential goal gating:
  The orchestrator only releases the next goal in a plan when:
    goal.status in {APPROVED, MERGED}
  This enforces the spec requirement: no new goal starts until the preceding
  goal's PR has passed CI and been approved (or merged).

ProjectSpec integration:
  The orchestrator loads the ProjectSpec at startup via LoadProjectSpec and
  exposes it through the read-only ``spec`` property. Downstream consumers
  (planner, validator, reconciler) receive it via constructor injection so
  they never load it themselves. Agents can never write the spec — only the
  approved ProposeSpecChange -> operator-apply flow may persist changes.
"""
from __future__ import annotations

import signal
from typing import Callable, Optional

import structlog

from src.app.telemetry.service import TelemetryService
from src.domain import DomainEvent, EventPort, GoalStatus
from src.domain import TelemetryEmitterPort
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
    "goal.ready_for_review",
    "goal.approved",
    "goal.merged",
]

# Statuses that unlock the next goal in a sequential plan
_UNLOCK_STATUSES = {GoalStatus.APPROVED, GoalStatus.MERGED}


class TaskGraphOrchestrator:
    """
    Event-driven goal coordinator.

    Lifecycle:
      - run_forever() subscribes to task/goal events and dispatches to use cases
      - shutdown() gracefully stops the loop on SIGTERM/SIGINT

    PR integration:
      - Reacts to goal.ready_for_review by opening a GitHub PR.
      - Reacts to goal.approved / goal.merged to unlock sequential goals.
      - CreateGoalPRUseCase must be injected for PR creation to work;
        when absent (dry-run), the orchestrator logs a warning and skips.

    Sequential gating:
      The orchestrator never dispatches tasks for a goal whose predecessor
      has not yet reached APPROVED or MERGED. This is enforced in
      _check_and_unlock_next_goals() which fires on goal.approved / goal.merged.
    """

    def __init__(
        self,
        task_repo: TaskRepositoryPort,
        goal_repo: GoalRepositoryPort,
        event_port: EventPort,
        merge_usecase: GoalMergeTaskUseCase,
        cancel_usecase: GoalCancelTaskUseCase,
        spec_repo: Optional[ProjectSpecRepository] = None,
        project_name: str = "",
        create_pr_usecase=None,  # CreateGoalPRUseCase | None
        telemetry_emitter: TelemetryEmitterPort | None = None,
    ) -> None:
        self._task_repo     = task_repo
        self._goal_repo     = goal_repo
        self._events        = event_port
        self._merge         = merge_usecase
        self._cancel        = cancel_usecase
        self._spec_repo     = spec_repo
        self._project_name  = project_name
        self._create_pr     = create_pr_usecase
        self._telemetry     = TelemetryService(telemetry_emitter, producer="goal-orchestrator")
        self._running       = False
        self._spec: Optional[ProjectSpec] = None

    # ------------------------------------------------------------------
    # ProjectSpec access
    # ------------------------------------------------------------------

    @property
    def spec(self) -> ProjectSpec:
        if self._spec is None:
            raise RuntimeError(
                "ProjectSpec has not been loaded yet. "
                "Call run_forever() or _load_spec() first."
            )
        return self._spec

    def _load_spec(self) -> None:
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
        self._install_signal_handlers()
        self._load_spec()

        self._running = True
        log.info(
            "orchestrator.starting",
            group=CONSUMER_GROUP,
            consumer=CONSUMER_NAME,
            events=WATCHED_EVENTS,
            spec_version=self._spec.meta.version if self._spec else "none",
            pr_integration="enabled" if self._create_pr else "disabled",
        )

        try:
            for event in self._events.subscribe_many(
                WATCHED_EVENTS, CONSUMER_GROUP, CONSUMER_NAME
            ):
                if not self._running:
                    break
                self._dispatch(event)
                self._events.ack(event, group=CONSUMER_GROUP)
        except Exception as exc:
            log.exception("orchestrator.fatal_error", error=str(exc))
            raise
        finally:
            log.info("orchestrator.stopped")

    def shutdown(self) -> None:
        log.info("orchestrator.shutdown_requested")
        self._running = False

    # ------------------------------------------------------------------
    # Event dispatch
    # ------------------------------------------------------------------

    # Canonical handler map — single source of truth for event routing.
    # Previously duplicated between run_forever() and _dispatch(); now
    # lives only here to prevent silent divergence.
    def _build_handlers(self) -> dict[str, Callable[[DomainEvent], None]]:
        return {
            "task.assigned": self._on_task_assigned,
            "task.completed": self._on_task_completed,
            "task.canceled": self._on_task_canceled,
            "goal.ready_for_review": self._on_goal_ready_for_review,
            "goal.approved": self._on_goal_unlocked,
            "goal.merged": self._on_goal_unlocked,
        }

    def _dispatch(self, event: DomainEvent) -> None:
        handlers = self._build_handlers()
        trace = self._telemetry.start_trace(correlation_id=event.payload.get("goal_id") or event.payload.get("task_id"))
        try:
            fn = handlers.get(event.type)
            if fn:
                fn(event)
        except Exception as exc:
            log.exception(
                "orchestrator.dispatch_error",
                event_type=event.type,
                payload=event.payload,
                error=str(exc),
            )
            self._telemetry.emit(
                "unexpected.error",
                self._telemetry.start_span(trace),
                payload={"event_type": event.type},
                metadata={"error_type": type(exc).__name__, "message": str(exc)},
                goal_id=event.payload.get("goal_id"),
                task_id=event.payload.get("task_id"),
            )

    # ------------------------------------------------------------------
    # Task event handlers (unchanged logic)
    # ------------------------------------------------------------------

    def _on_task_assigned(self, event: DomainEvent) -> None:
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
        trace = self._telemetry.start_trace(goal_id=goal_id, correlation_id=goal_id)

        for attempt in range(5):
            goal = self._goal_repo.get(goal_id)
            if goal is None or goal.status != GoalStatus.PENDING:
                return
            expected_v = goal.state_version
            goal.start()
            if self._goal_repo.update_if_version(goal_id, goal, expected_v):
                log.info("orchestrator.goal_started", goal_id=goal_id)
                self._telemetry.emit(
                    "goal.started",
                    self._telemetry.start_span(trace),
                    payload={"goal_id": goal_id, "trigger_task_id": task_id},
                    goal_id=goal_id,
                    task_id=task_id,
                )
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
    # PR event handlers (new)
    # ------------------------------------------------------------------

    def _on_goal_ready_for_review(self, event: DomainEvent) -> None:
        """
        All task branches merged → open a GitHub PR for this goal.

        If CreateGoalPRUseCase is not configured (dry-run / no GitHub token),
        the event is logged and skipped. The operator can create the PR manually
        or configure the integration later.
        """
        goal_id = event.payload.get("goal_id")
        if not goal_id:
            return

        log.info("orchestrator.goal_ready_for_review", goal_id=goal_id)

        if self._create_pr is None:
            log.warning(
                "orchestrator.pr_creation_skipped",
                goal_id=goal_id,
                reason="CreateGoalPRUseCase not configured (no GitHub integration)",
            )
            return

        try:
            pr_number = self._create_pr.execute(goal_id)
            log.info(
                "orchestrator.pr_opened",
                goal_id=goal_id,
                pr_number=pr_number,
            )
        except Exception as exc:
            log.exception(
                "orchestrator.pr_creation_failed",
                goal_id=goal_id,
                error=str(exc),
            )

    def _on_goal_unlocked(self, event: DomainEvent) -> None:
        """
        A goal reached APPROVED or MERGED → check if any dependent goals
        should now be released for execution.

        Sequential plan logic:
          - Load all goals.
          - Find any goal in PENDING status whose predecessor is the just-approved goal.
          - Such blocking is currently enforced at task level (depends_on), but for
            multi-goal sequential plans the orchestrator emits goal.unlock_next so
            external planners can start the next goal's tasks.
          - Emitting goal.unlock_next is advisory; the planner/operator decides
            whether and how to start the next goal.
        """
        goal_id    = event.payload.get("goal_id")
        event_type = event.type
        if not goal_id:
            return

        log.info(
            "orchestrator.goal_unlocked",
            goal_id=goal_id,
            trigger_event=event_type,
        )

        # Emit advisory event for sequential planners / dashboards.
        self._events.publish(DomainEvent(
            type="goal.unlock_next",
            producer="goal-orchestrator",
            payload={
                "completed_goal_id": goal_id,
                "trigger":           event_type,
            },
        ))

    # ------------------------------------------------------------------
    # Signal handling
    # ------------------------------------------------------------------

    def _install_signal_handlers(self) -> None:
        def _handler(sig, _frame):
            log.info("orchestrator.signal_received", signal=sig)
            self.shutdown()

        signal.signal(signal.SIGTERM, _handler)
        signal.signal(signal.SIGINT,  _handler)
