"""
src/infra/db/agent_event_reader.py — the read side of the agent_events stream.

The sink is write-only (best-effort, own connection). This reader serves the two
historical views the live SSE feed can't: per-plan / per-task event history
(GET /plans/{id}/agent-events) and the global metrics roll-up (GET /metrics).
Both are plain SELECTs on their own short session — never inside the plan UoW.

Metrics are computed in SQLite via json_extract over the stringified payload
(decision #33: telemetry rides the existing agent_events rows, no separate
store). Token counts live in llm.call rows; run/failure counts in the runner's
agent.started / agent.failed rows.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

_LIST_SQL = """
    SELECT id, event_id, plan_id, task_id, attempt, seq, type, payload, occurred_at
    FROM agent_events
    WHERE plan_id = :plan_id
    {task_clause}
    {before_clause}
    ORDER BY id DESC
    LIMIT :limit
"""

_METRICS_LLM_SQL = """
    SELECT
        COUNT(*) AS sessions,
        COALESCE(SUM(CAST(json_extract(payload, '$.llm_calls') AS INTEGER)), 0) AS calls,
        COALESCE(SUM(CAST(json_extract(payload, '$.prompt_tokens') AS INTEGER)), 0)
            AS prompt_tokens,
        COALESCE(
            SUM(CAST(json_extract(payload, '$.completion_tokens') AS INTEGER)), 0
        ) AS completion_tokens,
        COALESCE(SUM(CAST(json_extract(payload, '$.total_tokens') AS INTEGER)), 0)
            AS total_tokens
    FROM agent_events
    WHERE type = 'llm.call' {plan_clause}
"""

_METRICS_RUNS_SQL = """
    SELECT type, COUNT(*) AS n
    FROM agent_events
    WHERE type IN ('agent.started', 'agent.finished', 'agent.failed') {plan_clause}
    GROUP BY type
"""

_METRICS_FAILURE_KINDS_SQL = """
    SELECT COALESCE(json_extract(payload, '$.kind'), 'unknown') AS kind, COUNT(*) AS n
    FROM agent_events
    WHERE type = 'agent.failed' {plan_clause}
    GROUP BY kind
"""


class SqliteAgentEventReader:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def list(
        self,
        plan_id: str,
        *,
        task_id: str | None = None,
        limit: int = 200,
        before_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Most-recent-first page of a plan's events, optionally filtered to one
        task (task_id="" or None → the whole plan) and paged with before_id."""
        params: dict[str, Any] = {"plan_id": plan_id, "limit": limit}
        task_clause = ""
        if task_id:
            task_clause = "AND task_id = :task_id"
            params["task_id"] = task_id
        before_clause = ""
        if before_id is not None:
            before_clause = "AND id < :before_id"
            params["before_id"] = before_id
        sql = text(
            _LIST_SQL.format(task_clause=task_clause, before_clause=before_clause)
        )
        with self._sf() as session:
            rows = session.execute(sql, params).all()
        return [
            {
                "id": r[0],
                "event_id": r[1],
                "plan_id": r[2],
                "task_id": r[3],
                "attempt": r[4],
                "seq": r[5],
                "type": r[6],
                "payload": r[7],
                "occurred_at": r[8],
            }
            for r in rows
        ]

    def metrics(self, plan_id: str | None = None) -> dict[str, Any]:
        """Global (or per-plan) roll-up: LLM sessions/tokens, agent run counts,
        and failures grouped by kind (rate_limit visibility)."""
        plan_clause = "AND plan_id = :plan_id" if plan_id else ""
        params = {"plan_id": plan_id} if plan_id else {}
        with self._sf() as session:
            llm = session.execute(
                text(_METRICS_LLM_SQL.format(plan_clause=plan_clause)), params
            ).one()
            runs: dict[str, int] = {
                str(r[0]): int(r[1])
                for r in session.execute(
                    text(_METRICS_RUNS_SQL.format(plan_clause=plan_clause)), params
                ).all()
            }
            failures: dict[str, int] = {
                str(r[0]): int(r[1])
                for r in session.execute(
                    text(_METRICS_FAILURE_KINDS_SQL.format(plan_clause=plan_clause)),
                    params,
                ).all()
            }
        return {
            "llm": {
                "sessions": int(llm[0]),
                "calls": int(llm[1]),
                "prompt_tokens": int(llm[2]),
                "completion_tokens": int(llm[3]),
                "total_tokens": int(llm[4]),
            },
            "agent": {
                "runs": runs.get("agent.started", 0),
                "finished": runs.get("agent.finished", 0),
                "failed": runs.get("agent.failed", 0),
                "failures_by_kind": failures,
            },
        }
