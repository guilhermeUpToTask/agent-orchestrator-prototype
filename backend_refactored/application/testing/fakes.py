"""In-memory test doubles for the application layer. Let advance_plan be tested
end-to-end with ZERO infrastructure (no SQLite, no pi, no network). The whole
orchestration loop runs against these; production swaps them for real adapters
behind the same ports."""

from __future__ import annotations

from dataclasses import dataclass

from domain.aggregates.planner_orchestrator import Plan
from domain.entities.agent_spec import AgentSpec
from domain.entities.task import Task
from domain.errors.agent_errors import AgentNotFoundError, NoDefaultAgentError
from domain.errors.tasks_errors import StaleVersionError
from domain.events.agent_events import AgentEvent
from domain.events.base import DomainEvent
from domain.value_objects.tasks_vos import TaskResult

from application.ports import TaskFailed, UnitOfWork


# ---- in-memory plan repository with version-CAS ----
class InMemoryPlanRepository:
    def __init__(self) -> None:
        self._store: dict[str, Plan] = {}
        self._requests: dict[str, str] = {}
        self._claims: dict[str, str] = {}

    def add(self, plan: Plan) -> None:
        self._store[plan.id] = plan.model_copy(deep=True)

    def get(self, plan_id: str) -> Plan:
        return self._store[plan_id].model_copy(deep=True)

    def save(self, plan: Plan) -> None:
        current = self._store.get(plan.id)
        if current is not None and current.version >= plan.version:
            # optimistic-lock: stored version moved at/ahead of what we based on
            raise StaleVersionError(plan.id, plan.version, current.version)
        self._store[plan.id] = plan.model_copy(deep=True)

    # --- request_id idempotency ---
    def find_by_request_id(self, request_id: str) -> str | None:
        return self._requests.get(request_id)

    def bind_request_id(self, request_id: str, plan_id: str) -> None:
        self._requests[request_id] = plan_id

    # --- lease (simplified, in-memory) ---
    def claim_one_unit(self, worker_id: str, lease_seconds: int) -> Plan | None:
        for plan in self._store.values():
            if plan.phase.value in ("executing", "drafting", "breakdown", "enriching"):
                claimed = self._claims.get(plan.id)
                if claimed is None or claimed != worker_id:
                    self._claims[plan.id] = worker_id
                    return plan.model_copy(deep=True)
        return None

    def heartbeat(self, plan_id: str, worker_id: str) -> None:
        self._claims[plan_id] = worker_id

    def release(self, plan_id: str, worker_id: str) -> None:
        self._claims.pop(plan_id, None)


# ---- in-memory outbox ----
class InMemoryOutbox:
    def __init__(self) -> None:
        self.events: list[DomainEvent] = []
        self._staged: list[DomainEvent] = []

    def add(self, event: DomainEvent) -> None:
        self._staged.append(event)

    def _commit(self) -> None:
        self.events.extend(self._staged)
        self._staged = []

    def _rollback(self) -> None:
        self._staged = []

    def types(self) -> list[str]:
        return [e.event_type for e in self.events]


# ---- in-memory unit of work (transaction boundary) ----
class InMemoryUnitOfWork:
    def __init__(self, repo: InMemoryPlanRepository, outbox: InMemoryOutbox) -> None:
        self.plans = repo
        self.outbox = outbox

    def __enter__(self) -> "InMemoryUnitOfWork":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.outbox._commit()  # state + events commit together
        else:
            self.outbox._rollback()  # rollback discards staged events


# ---- in-memory agent registry ----
class InMemoryAgentRepository:
    def __init__(self, agents: list[AgentSpec], default_id: str | None = None) -> None:
        self._agents = {a.id: a for a in agents}
        self._default = default_id

    def get(self, agent_id: str) -> AgentSpec:
        if agent_id not in self._agents:
            raise AgentNotFoundError(agent_id)
        return self._agents[agent_id]

    def list(self) -> list[AgentSpec]:
        return list(self._agents.values())

    def default_agent_id(self) -> str:
        if self._default is None:
            raise NoDefaultAgentError()
        return self._default


# ---- no-op workspace (git seam) ----
@dataclass
class _Handle:
    path: str = "/tmp/shared"


class NoOpWorkspace:
    def __init__(self) -> None:
        self.committed: list[str] = []
        self.discarded: list[str] = []

    async def begin(self, plan_id: str, task_id: str, attempt: int) -> _Handle:
        return _Handle()

    async def commit(self, handle: _Handle) -> None:
        self.committed.append(handle.path)

    async def discard(self, handle: _Handle) -> None:
        self.discarded.append(handle.path)


# ---- collecting event sink ----
class CollectingEventSink:
    def __init__(self) -> None:
        self.events: list[AgentEvent] = []

    async def emit(self, event: AgentEvent) -> None:
        self.events.append(event)


# ---- scriptable dummy agent runner ----
@dataclass
class DummyBehavior:
    """How the dummy should behave for a given task id."""

    output: str = "ok"
    fail_times: int = 0  # fail the first N attempts, then succeed
    fail_reason: str = "transient"
    always_fail: bool = False
    emit_events: int = 0  # number of fake agent events to stream
    crash_after_success: bool = False  # simulate worker death AFTER agent returned


class DummyAgentRunner:
    """Implements AgentRunner with no LLM/subprocess. Scriptable per task id so
    tests deterministically drive success / retry / permanent-failure / crash.
    Counts calls so tests can assert the agent was/ wasn't invoked (idempotency)."""

    def __init__(self, script: dict[str, DummyBehavior] | None = None) -> None:
        self.script = script or {}
        self.calls: dict[str, int] = {}

    async def run(
        self,
        task: Task,
        spec: AgentSpec,
        *,
        idempotency_key: str,
        event_sink,
        workspace,
    ) -> TaskResult:
        self.calls[task.id] = self.calls.get(task.id, 0) + 1
        b = self.script.get(task.id, DummyBehavior())

        for i in range(b.emit_events):
            await event_sink.emit(
                AgentEvent(
                    plan_id=idempotency_key.split(":")[0],
                    task_id=task.id,
                    attempt=task.attempt,
                    seq=i,
                    type="step",
                    payload={"i": str(i)},
                )
            )

        if b.always_fail:
            raise TaskFailed(reason=b.fail_reason)
        if b.fail_times and task.attempt <= b.fail_times:
            raise TaskFailed(reason=b.fail_reason)

        return TaskResult.success(b.output, metadata={"dummy": "true"})


# ---- controllable clock for deterministic time/backoff tests ----
from datetime import datetime, timedelta, timezone


class FakeClock:
    """A clock the test drives. advance() moves time forward so backoff gates
    can be crossed deterministically without real sleeping."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)
