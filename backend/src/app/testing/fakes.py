"""In-memory test doubles for the application layer. Let advance_plan be tested
end-to-end with ZERO infrastructure (no SQLite, no pi, no network). The whole
orchestration loop runs against these; production swaps them for real adapters
behind the same ports — so the fakes' claim/lease/CAS semantics deliberately
mirror what the real SQLite adapter must do."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.domain.aggregates.planner_orchestrator import (
    Plan,
    WORKER_CLAIMABLE_PHASES,
)
from src.domain.entities.agent_spec import AgentSpec
from src.domain.entities.capability import Capability
from src.domain.entities.task import Task
from src.domain.errors.agent_errors import AgentNotFoundError, NoDefaultAgentError
from src.domain.errors.planning_errors import PlanNotFoundError
from src.domain.errors.tasks_errors import StaleVersionError
from src.domain.events.agent_events import AgentEvent
from src.domain.events.base import DomainEvent
from src.domain.value_objects.lifecycle import FailureKind
from src.domain.value_objects.tasks_vos import TaskResult

from src.app.ports import (
    AgentEventSink,
    Clock,
    TaskFailed,
    UnitOfWork,
    WorkspaceHandle,
)


# ---- controllable clock for deterministic time/backoff/lease tests ----
class FakeClock:
    """A clock the test drives. advance() moves time forward so backoff gates and
    lease expiries can be crossed deterministically without real sleeping."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)


@dataclass
class _Claim:
    worker_id: str
    expires_at: datetime
    lease_seconds: int


# ---- in-memory plan repository with version-CAS + lease semantics ----
class InMemoryPlanRepository:
    """Mirrors the real adapter's contracts: detached aggregates (deep copy on
    get/save), version-CAS, and a lease with REAL expiry — a plan claimed by a
    live worker is not claimable by anyone (including re-claims); it becomes
    claimable again only when released or when the lease expires (crash recovery).
    The claim predicate is the driver model: only worker-claimable phases
    (ARCHITECTURE / ENRICHING / RUNNING) are ever selected."""

    def __init__(self, clock: Clock | None = None) -> None:
        self._store: dict[str, Plan] = {}
        self._requests: dict[str, str] = {}
        self._claims: dict[str, _Claim] = {}
        self._clock: Clock = clock or FakeClock()

    def add(self, plan: Plan) -> None:
        self._store[plan.id] = plan.model_copy(deep=True)

    def get(self, plan_id: str) -> Plan:
        if plan_id not in self._store:
            raise PlanNotFoundError(plan_id)
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

    # --- lease ---
    def claim_one_unit(self, worker_id: str, lease_seconds: int) -> Plan | None:
        now = self._clock.now()
        for plan in self._store.values():
            if plan.phase not in WORKER_CLAIMABLE_PHASES:
                continue  # gates + conversational phases are invisible to workers
            claim = self._claims.get(plan.id)
            if claim is not None and claim.expires_at > now:
                continue  # live lease (even our own): not claimable
            self._claims[plan.id] = _Claim(
                worker_id=worker_id,
                expires_at=now + timedelta(seconds=lease_seconds),
                lease_seconds=lease_seconds,
            )
            return plan.model_copy(deep=True)
        return None

    def heartbeat(self, plan_id: str, worker_id: str) -> None:
        claim = self._claims.get(plan_id)
        if claim is not None and claim.worker_id == worker_id:
            claim.expires_at = self._clock.now() + timedelta(
                seconds=claim.lease_seconds
            )

    def release(self, plan_id: str, worker_id: str) -> None:
        claim = self._claims.get(plan_id)
        if claim is not None and claim.worker_id == worker_id:
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

    def __exit__(self, *exc: object) -> None:
        if exc[0] is None:
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


# ---- in-memory capability catalog ----
class InMemoryCapabilityRepository:
    def __init__(self, capabilities: list[Capability] | None = None) -> None:
        self._caps = {c.id: c for c in (capabilities or [])}

    def get(self, capability_id: str) -> Capability:
        return self._caps[capability_id]

    def list(self) -> list[Capability]:
        return list(self._caps.values())

    def add(self, capability: Capability) -> None:
        self._caps[capability.id] = capability

    def update(self, capability: Capability) -> None:
        self._caps[capability.id] = capability

    def delete(self, capability_id: str) -> None:
        self._caps.pop(capability_id, None)


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
    fail_kind: FailureKind = FailureKind.CONNECTION_ERROR  # retryable by default
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
        event_sink: AgentEventSink,
        workspace: WorkspaceHandle,
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
            raise TaskFailed(reason=b.fail_reason, kind=b.fail_kind)
        if b.fail_times and task.attempt <= b.fail_times:
            raise TaskFailed(reason=b.fail_reason, kind=b.fail_kind)

        return TaskResult.success(b.output, metadata={"dummy": "true"})


__all__ = [
    "FakeClock",
    "InMemoryPlanRepository",
    "InMemoryOutbox",
    "InMemoryUnitOfWork",
    "InMemoryAgentRepository",
    "InMemoryCapabilityRepository",
    "NoOpWorkspace",
    "CollectingEventSink",
    "DummyBehavior",
    "DummyAgentRunner",
    "UnitOfWork",
]
