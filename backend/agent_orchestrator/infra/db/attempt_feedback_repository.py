"""
src/infra/db/attempt_feedback_repository.py — the PriorAttemptFeedback port.

Reads the previous attempt's rejection so the next attempt's prompt can state
it. Its own short read transaction, never the plan UnitOfWork: the agent runner
executes OUTSIDE transactions by design, and a read that says "why did the last
candidate fail" must never be able to roll plan state back.

Nothing new is written. `ExecutionAttempt.safe_message` has always carried the
orchestrator's rejection; it was simply never read back into a prompt, so every
retry re-ran an identical prompt and reproduced an identical failure.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from agent_orchestrator.app.agent_feedback import is_agent_actionable, split_reasons
from agent_orchestrator.app.ports import PriorAttemptRejection

# The most recent FAILED attempt for this exact task revision. Revision matters:
# an edited task is a different contract, so an older rejection describes work
# the agent is no longer being asked to do.
_SELECT_SQL = text(
    """
    SELECT number, safe_message
    FROM execution_attempts
    WHERE plan_id = :plan_id
      AND goal_id = :goal_id
      AND task_id = :task_id
      AND status = 'failed'
      AND safe_message IS NOT NULL
    ORDER BY number DESC
    LIMIT 5
    """
)


class SqliteAttemptFeedbackRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def last_rejection(
        self, plan_id: str, goal_id: str, task_id: str, *, task_revision: int
    ) -> PriorAttemptRejection | None:
        with self._sf() as session:
            rows = session.execute(
                _SELECT_SQL,
                {"plan_id": plan_id, "goal_id": goal_id, "task_id": task_id},
            ).all()

        # Scan back a few attempts: the most recent failure is often a provider
        # rate limit, which says nothing an agent could act on, while the
        # candidate rejection behind it still does. Observed live — attempt 1
        # rate-limited, attempt 2 rejected the candidate.
        for number, safe_message in rows:
            message = str(safe_message)
            if not is_agent_actionable(message):
                continue
            return PriorAttemptRejection(
                attempt_number=int(number),
                reasons=tuple(split_reasons(message)),
            )
        return None


__all__ = ["SqliteAttemptFeedbackRepository"]
