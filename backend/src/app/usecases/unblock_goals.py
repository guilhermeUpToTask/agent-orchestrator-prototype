"""
src/app/usecases/unblock_goals.py — Unblock dependent goals use case.

When a goal reaches MERGED, scan all PENDING goals that declared it as a
prerequisite and start any that are now fully unblocked (all depends_on
names are in the merged set).

This is the goal-level mirror of TaskUnblockUseCase.  The same O(N) scan
optimisation applies: merged_goal_names is built once and reused across all
candidates rather than reloading the full list per goal.

Triggered by:
  - AdvanceGoalFromPRUseCase emitting goal.merged
  - Any other path that transitions a goal to MERGED status

The use case calls goal.start() on each newly unblocked goal and emits
goal.unblocked so the orchestrator can react (e.g. begin task creation).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from src.domain import DomainEvent, EventPort
from src.domain.aggregates.goal import GoalStatus
from src.domain.repositories.goal_repository import GoalRepositoryPort

log = structlog.get_logger(__name__)

PRODUCER = "goal-orchestrator"
MAX_CAS_RETRIES = 5


@dataclass
class UnblockGoalsResult:
    merged_goal_name: str
    unblocked: list[str] = field(default_factory=list)  # goal_ids that were started
    still_blocked: list[str] = field(default_factory=list)  # deps still unmet

    @property
    def count(self) -> int:
        return len(self.unblocked)


class UnblockGoalsUseCase:
    """
    Scan for PENDING goals that depended on the just-merged goal and start
    any that are now fully unblocked.

    Dependency matching uses goal *names* (not IDs) because that is what
    GoalSpec.depends_on stores and what planners reason about.
    """

    def __init__(
        self,
        goal_repo: GoalRepositoryPort,
        event_port: EventPort,
    ) -> None:
        self._goal_repo = goal_repo
        self._events = event_port

    def execute(self, merged_goal_id: str) -> UnblockGoalsResult:
        all_goals = self._goal_repo.list_all()

        # Build the set of merged goal *names* — dependency keys are names.
        merged_goal_names: set[str] = {
            g.name for g in all_goals if g.status == GoalStatus.MERGED
        }

        # Resolve the name of the just-merged goal for logging.
        merged_goal = next((g for g in all_goals if g.goal_id == merged_goal_id), None)
        merged_name = merged_goal.name if merged_goal else merged_goal_id

        log.info(
            "unblock_goals.scanning",
            merged_goal_id=merged_goal_id,
            merged_name=merged_name,
        )

        result = UnblockGoalsResult(merged_goal_name=merged_name)

        for goal in all_goals:
            if goal.status != GoalStatus.PENDING:
                continue
            if merged_name not in goal.depends_on:
                continue  # doesn't depend on the just-merged goal at all

            if goal.is_blocked(merged_goal_names):
                log.info(
                    "unblock_goals.still_blocked",
                    goal_id=goal.goal_id,
                    unmet=[d for d in goal.depends_on if d not in merged_goal_names],
                )
                result.still_blocked.append(goal.goal_id)
                continue

            # All prerequisites met — start the goal.
            started = self._start_goal(goal.goal_id)
            if started:
                result.unblocked.append(goal.goal_id)
            else:
                result.still_blocked.append(goal.goal_id)

        log.info(
            "unblock_goals.done",
            merged_goal_id=merged_goal_id,
            unblocked=result.unblocked,
            still_blocked=result.still_blocked,
        )
        return result

    # ------------------------------------------------------------------
    # Internal: CAS-safe goal start
    # ------------------------------------------------------------------

    def _start_goal(self, goal_id: str) -> bool:
        """
        Transition goal PENDING → RUNNING via CAS, emit goal.unblocked.
        Returns True on success, False if the goal moved on between checks.
        """
        for attempt in range(MAX_CAS_RETRIES):
            goal = self._goal_repo.get(goal_id)
            if goal is None or goal.status != GoalStatus.PENDING:
                return False  # already started or gone

            expected_v = goal.state_version
            goal.start()

            if self._goal_repo.update_if_version(goal_id, goal, expected_v):
                self._events.publish(DomainEvent(
                    type="goal.unblocked",
                    producer=PRODUCER,
                    payload={
                        "goal_id": goal_id,
                        "name": goal.name,
                        "feature_tag": goal.feature_tag,
                    },
                ))
                log.info(
                    "unblock_goals.goal_started",
                    goal_id=goal_id,
                    name=goal.name,
                )
                return True

            log.warning(
                "unblock_goals.cas_conflict",
                goal_id=goal_id,
                attempt=attempt,
            )

        log.error("unblock_goals.cas_exhausted", goal_id=goal_id)
        return False
