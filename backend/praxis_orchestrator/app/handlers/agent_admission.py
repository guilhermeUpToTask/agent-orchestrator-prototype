"""Which agent runs a task, and whether it may start right now.

Extracted from `ExecutionHandler` (P8.7 task 3, step 2). Two questions that were
answered inside the pull-scan loop and are really one cohesive concern:

- **Selection** — the preferred spec comes from the task's binding; if that
  provider is unavailable, a capability-satisfying sibling in an allowed tier may
  run instead. This is a RUNTIME SUBSTITUTION and never touches the persisted
  binding, which is the operator's preference and the aggregate's to own.
- **Admission** — an attempt that would exceed the provider's in-flight ceiling,
  or that runs against an open `RuntimeCircuit`, does not start at all.

The two cannot be separated: selection asks "is this provider free?" using the
same circuit and in-flight facts admission enforces, so splitting them would
duplicate the reads and let the answers drift.

The collaborator owns the agent catalog, the provider catalog and the routing
policy — after the move `ExecutionHandler` reads none of the three directly.
Every method takes the `UnitOfWork` as an argument: the caller owns the
transaction, exactly as it did when these were handler methods.

It also owns the WRITE side of the `RuntimeCircuit` rows it reads
(`record_capacity_failure`, `clear_circuit`, added in step 4). A circuit that is
opened by one module and consulted by another is how the two drift; keeping the
half-open probe's claim, release and rewrite in one file is what makes
single-flight probing checkable at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from praxis_orchestrator.domain.aggregates.planner_orchestrator import Plan
from praxis_orchestrator.domain.entities.agent_spec import AgentSpec
from praxis_orchestrator.domain.entities.goal import Goal
from praxis_orchestrator.domain.entities.planning_artifacts import PlanBlock
from praxis_orchestrator.domain.entities.task import Task
from praxis_orchestrator.domain.errors.base import DomainError
from praxis_orchestrator.domain.events.outbox import PlanBlocked
from praxis_orchestrator.domain.factories.identity import new_id
from praxis_orchestrator.domain.repositories.agent_repo import AgentRepository
from praxis_orchestrator.domain.repositories.model_provider_repo import ModelProviderRepository

from praxis_orchestrator.app.block_policy import resolutions_for
from praxis_orchestrator.app.execution_records import RuntimeCircuit
from praxis_orchestrator.app.handlers.base import Signal
from praxis_orchestrator.app.handlers.execution_rules import run_role_for
from praxis_orchestrator.app.ports import Clock, UnitOfWork
from praxis_orchestrator.app.provider_capacity import (
    CAPACITY_KINDS,
    CapacityScope,
    ProviderCapacityPolicy,
    RoutingPolicy,
    circuit_model_id,
    circuit_ref,
    resolve_capacity_scope,
    resolve_max_inflight,
)
from praxis_orchestrator.app.runtime_failures import LimitScope, RuntimeFailure
from praxis_orchestrator.domain.value_objects.lifecycle import FailureKind


@dataclass(frozen=True)
class CapacityOutcome:
    """What recording a failure against the provider's circuit decided.

    `latched` — the outage has run past its wall-clock ceiling and now needs a
    human; it vetoes the retry. `waiting` — a capacity failure inside the
    ceiling, which BYPASSES the per-task retry budget, because the budget counts
    the agent's mistakes and there was no room upstream to make one. `ref` names
    the circuit row that was actually written, so a block's evidence points at a
    row that exists.
    """

    latched: bool = False
    waiting: bool = False
    ref: str | None = None


class AgentAdmission:
    """Agent selection and provider admission for one execution attempt."""

    def __init__(
        self,
        agents: AgentRepository,
        clock: Clock,
        capacity: ProviderCapacityPolicy,
        providers: ModelProviderRepository | None = None,
        routing: RoutingPolicy | None = None,
    ) -> None:
        self._agents = agents
        self._clock = clock
        self._capacity = capacity
        # Optional: without it, capacity metadata falls back to the global policy.
        # Dry-run and unit tests need no provider catalog.
        self._providers = providers
        self._routing = routing or RoutingPolicy()

    def resolve_spec(self, plan: Plan, task: Task) -> AgentSpec:
        run_role = run_role_for(plan, task)
        agent_id = task.role_agent_ids.get(run_role, task.agent_id)
        return (
            self._agents.get(agent_id)
            if agent_id
            else self._agents.get(self._agents.default_agent_id())
        )

    def provider_metadata(self, spec: AgentSpec) -> tuple[int, CapacityScope]:
        """(in-flight cap, capacity scope) for this spec's provider.

        Cap precedence: model row override -> provider row -> global default. The
        cap is provider DATA because it varies enormously — a paid tier, a free
        aggregator, and a local single-GPU server share no sensible number, and
        one global value would throttle the first or over-drive the last.

        A missing provider row is not fatal here: the runner factory already
        fail-fasts on a broken binding, so falling back keeps this off the
        critical path.
        """
        if self._providers is None or not spec.provider_id:
            return self._capacity.max_inflight, CapacityScope.PER_MODEL
        try:
            provider = self._providers.get(spec.provider_id)
        except DomainError:
            return self._capacity.max_inflight, CapacityScope.PER_MODEL
        scope = resolve_capacity_scope(provider.capacity_scope)
        model = provider.get_model(spec.model_id) if spec.model_id else None
        cap = resolve_max_inflight(
            model_cap=model.max_inflight if model is not None else None,
            provider_cap=provider.max_inflight,
            default=self._capacity.max_inflight,
        )
        return cap, scope

    def _spec_wait_seconds(self, spec: AgentSpec, uow: UnitOfWork) -> float | None:
        """How long this spec's provider is unavailable for, or None if it is free.

        `inf` means "blocked, not merely waiting" (a latched circuit): no amount of
        waiting clears it, so routing elsewhere is always preferable.
        `0.0` means "at its in-flight ceiling right now" — nothing to wait for,
        another pool may well be idle.
        """
        if not spec.provider_id or not spec.model_id:
            return None
        circuit = uow.executions.get_runtime_circuit(
            spec.runtime_type, spec.provider_id, None
        ) or uow.executions.get_runtime_circuit(spec.runtime_type, spec.provider_id, spec.model_id)
        if circuit is not None:
            if circuit.manual_intervention:
                return float("inf")
            remaining = (circuit.retry_at - self._clock.now()).total_seconds()
            if remaining > 0:
                return remaining
        if self.admission_signal(spec, uow) is not None:
            return 0.0
        return None

    def select_spec(self, plan: Plan, task: Task, uow: UnitOfWork) -> AgentSpec:
        """The spec this attempt should actually run on.

        A RUNTIME SUBSTITUTION, never a re-binding: the task's persisted
        `agent_id`/`role_agent_ids` are untouched, because those are the aggregate's
        to own and they record the operator's PREFERENCE. What actually ran is
        recorded on the ExecutionAttempt, which already carries provider/model, so
        every attempt stays auditable.

        Falls back to the preferred spec whenever no better candidate exists, which
        keeps single-agent setups on exactly the path they had before.
        """
        preferred = self.resolve_spec(plan, task)
        wait = self._spec_wait_seconds(preferred, uow)
        if wait is None:
            return preferred  # available: preference wins, no questions asked
        # A short circuit wait is worth waiting out rather than degrading output.
        # An at-capacity provider (wait == 0) substitutes immediately.
        if 0.0 < wait <= self._routing.downgrade_after_seconds:
            return preferred

        alternative = self.free_alternative(task, preferred, uow)
        # Everything capable is throttled: keep the preferred spec so the existing
        # circuit/admission checks produce the same wait or block they always did.
        return alternative or preferred

    def free_alternative(
        self, task: Task, preferred: AgentSpec, uow: UnitOfWork
    ) -> AgentSpec | None:
        """The best-tier interchangeable agent whose provider is free RIGHT NOW.

        Interchangeable means: a different agent, in the same declared role, that
        satisfies every capability this task requires, in a tier routing allows.
        Tier order is preference order, so a substitution never reaches past a
        better model that was also available.

        `None` means there is nowhere to go — which is a real answer and not the
        same as "no substitution wanted". Callers use it to decide whether
        waiting is the only option left (P8.6 Task 3), so it must not silently
        fall back to the preferred spec the way `select_spec` does.
        """
        required = list(task.required_capabilities)
        candidates = [
            agent
            for agent in self._agents.list()
            if agent.id != preferred.id
            and agent.role == preferred.role
            and set(required).issubset({capability.id for capability in agent.capabilities})
            and self._routing.allows(agent.model_role)
        ]
        candidates.sort(key=lambda agent: self._routing.tier_rank(agent.model_role))
        for candidate in candidates:
            if self._spec_wait_seconds(candidate, uow) is None:
                return candidate
        return None

    def admission_signal(self, spec: AgentSpec, uow: UnitOfWork) -> Signal | None:
        """Refuse to START an attempt that would exceed the provider's in-flight
        ceiling. Turns "fire the 33rd request, get refused, back off" into "never
        fire the 33rd" -- the refusals were never necessary, only unmeasured.

        The count is CROSS-PLAN because the upstream pool is: a per-plan count
        would let two plans each open a full cap.
        """
        if not spec.provider_id or not spec.model_id:
            return None
        cap, scope = self.provider_metadata(spec)
        # An endpoint-wide provider shares one pool across its models, so count
        # every model on it; otherwise each routed model has its own pool.
        counted_model = None if scope is CapacityScope.ENDPOINT_WIDE else spec.model_id
        inflight = uow.executions.count_inflight_attempts(
            spec.runtime_type, spec.provider_id, counted_model
        )
        if inflight >= cap:
            return Signal.NOT_READY
        return None

    def circuit_signal(
        self,
        plan_id: str,
        plan: Plan,
        goal: Goal,
        task: Task,
        spec: AgentSpec,
        uow: UnitOfWork,
    ) -> Signal | None:
        if not spec.provider_id or not spec.model_id:
            return None
        # Two circuits can gate one call. The provider-wide one (account-level
        # quota) is checked FIRST because routing to another model cannot escape
        # it; the per-model one covers this model's upstream pool. Whichever is
        # open wins, and a provider-wide block outranks a per-model wait.
        circuit = uow.executions.get_runtime_circuit(
            spec.runtime_type, spec.provider_id, None
        ) or uow.executions.get_runtime_circuit(spec.runtime_type, spec.provider_id, spec.model_id)
        if circuit is None:
            return None
        if circuit.manual_intervention:
            block = PlanBlock(
                id=new_id(),
                kind="provider_capacity",
                explanation=circuit.safe_message,
                stage=task.tdd_stage,
                goal_id=goal.id,
                task_id=task.id,
                task_revision=task.revision,
                legal_resolutions=resolutions_for("provider_capacity"),
                evidence_refs=[circuit_ref(circuit.runtime, circuit.provider_id, circuit.model_id)],
                created_at=self._clock.now(),
            )
            plan.open_block(block)
            plan.bump_version()
            uow.outbox.add(
                PlanBlocked(
                    plan_id=plan_id,
                    block_id=block.id,
                    stage=block.stage,
                    goal_id=goal.id,
                    task_id=task.id,
                    task_revision=task.revision,
                )
            )
            uow.plans.save(plan)
            return Signal.PAUSED
        now = self._clock.now()
        if now < circuit.retry_at:
            return Signal.NOT_READY
        # HALF-OPEN PROBE, SINGLE-FLIGHT. Past retry_at exactly one runner may
        # proceed. This used to be a bare `return None`, which every concurrent
        # goal worker reached at once: with four goals in flight, one outage
        # window cost four failures instead of one and inflated the circuit four
        # times as fast as the outage warranted. The claim is a conditional UPDATE
        # inside this transaction, so the losers see it taken and keep waiting.
        # The record is kept until success so a failed probe increments the
        # durable failure count instead of silently resetting the circuit.
        if not uow.executions.try_claim_circuit_probe(
            circuit.runtime,
            circuit.provider_id,
            circuit.model_id,
            holder=f"{plan_id}:{goal.id}:{task.id}:{task.attempt}",
            now=now,
            stale_before=now - timedelta(seconds=self._capacity.probe_stale_after_seconds),
        ):
            return Signal.NOT_READY
        return None

    def record_capacity_failure(
        self,
        spec: AgentSpec,
        kind: FailureKind,
        failure: RuntimeFailure,
        safe_message: str,
        not_before: datetime | None,
        uow: UnitOfWork,
    ) -> CapacityOutcome:
        """Record a failed attempt against this provider's circuit.

        The write side of the rows `circuit_signal` and `_spec_wait_seconds`
        read — which is why it lives here rather than in the finalizer that
        calls it. Runs INSIDE the caller's open transaction, exactly as it did
        when it was inline: it opens none of its own.
        """
        if (
            # CONNECTION_ERROR is capacity too: an unreachable or
            # overloaded endpoint is not a defect in this task, and
            # exhausting the per-task budget against it used to open a
            # goal block for something no human edit could fix. Safe to
            # widen only now that the ceiling above bounds the wait --
            # doing it while the attempt-count latch was live would have
            # escalated after five shared failures instead.
            kind in CAPACITY_KINDS
            and spec.provider_id
            and spec.model_id
            and not_before is not None
            # A CONCURRENCY cap is not an outage: the provider served
            # every other in-flight request successfully and refused only
            # the one over the ceiling. The remedy is "send fewer at
            # once", so this attempt requeues on its own short backoff and
            # NO circuit opens -- opening one would halt a provider that
            # is working, and stall the siblings that are mid-run.
            # The failure is still recorded on the attempt for telemetry.
            and failure.limit_scope is not LimitScope.REQUEST_CONCURRENCY
        ):
            # Account-level limits key provider-wide; upstream-level ones
            # key per model. Routing to a sibling model escapes the second
            # but never the first.
            _, capacity_scope = self.provider_metadata(spec)
            circuit_model = circuit_model_id(spec.model_id, failure.limit_scope, capacity_scope)
            capacity_ref = circuit_ref(spec.runtime_type, spec.provider_id, circuit_model)
            existing = uow.executions.get_runtime_circuit(
                spec.runtime_type,
                spec.provider_id,
                circuit_model,
            )
            failure_count = (existing.failure_count if existing else 0) + 1
            # The START of the outage, preserved across updates within one
            # outage (restamping every failure would peg the age at ~0 and
            # make the ceiling unreachable) but RESET when the previous
            # circuit has been quiet long enough to count as a past outage.
            # A row abandoned by an earlier session would otherwise pre-age
            # a fresh outage straight past its ceiling — observed live
            # against a circuit left behind three days earlier.
            scope_value = failure.limit_scope.value if failure.limit_scope is not None else None
            opened_at = self._capacity.outage_start(
                existing.opened_at if existing else None,
                existing.retry_at if existing else None,
                self._clock.now(),
                scope_value,
            )
            # ESCALATE ON DURATION, NOT ON A COUNT. The old rule compared
            # this provider-global `failure_count` against a PER-TASK
            # attempt budget -- different units of measure. It latched
            # after a handful of failures shared across concurrent goals
            # while each task had barely retried, and it ignored the
            # operator's raised `retry_max_attempts` entirely (see
            # RetryPolicy.should_retry, which deliberately treats that key
            # as a floor). `failure_count` is kept for telemetry only.
            latched = self._capacity.outage_exceeded(opened_at, self._clock.now(), scope_value)
            uow.executions.upsert_runtime_circuit(
                RuntimeCircuit(
                    runtime=spec.runtime_type,
                    provider_id=spec.provider_id,
                    model_id=circuit_model,
                    failure_count=failure_count,
                    opened_at=opened_at,
                    retry_at=not_before,
                    last_failure_kind=kind.value,
                    safe_message=safe_message,
                    manual_intervention=latched,
                    limit_scope=scope_value,
                    # probe_holder/probe_started_at default to None, so
                    # rewriting the row RELEASES this attempt's probe --
                    # load-bearing, and the reason the next window is
                    # probeable at all.
                )
            )
            # Inside the ceiling a capacity failure must ALSO bypass the
            # per-task retry budget. Removing only the circuit latch would
            # have changed nothing: the task still exhausted
            # kind_max_attempts and reached fail_task, opening the same
            # goal block a few attempts later. Waiting is bounded by the
            # ceiling above, which is the whole point of the redesign.
            return CapacityOutcome(latched=latched, waiting=not latched, ref=capacity_ref)
        if spec.provider_id and spec.model_id:
            # This attempt may have held the half-open probe and then
            # failed for a NON-capacity reason (the provider answered; the
            # run failed on its own merits). Release the probe explicitly
            # so the next window is probeable immediately instead of
            # waiting out the stale timeout.
            for probe_key in (spec.model_id, None):
                uow.executions.release_circuit_probe(
                    spec.runtime_type, spec.provider_id, probe_key
                )
        return CapacityOutcome()

    @staticmethod
    def clear_circuit(uow: UnitOfWork, spec: AgentSpec) -> None:
        """A successful run proves the provider recovered — retire its circuit.

        Must be called from EVERY success finalizer. It used to live only in
        `_finalize_success` (the legacy, non-cyclic path), so a cyclic plan
        never cleared it: `failure_count` accumulated transient rate limits
        across an entire run until it latched `manual_intervention`, opening a
        provider_capacity block that only a human `wait_and_retry` could reset.
        """
        if spec.provider_id and spec.model_id:
            # Clear BOTH tiers: a completed run proves the account has budget and
            # this model's upstream pool has room. Leaving the provider-wide row
            # behind would keep every sibling model gated on a resolved outage.
            uow.executions.clear_runtime_circuit(
                spec.runtime_type,
                spec.provider_id,
                spec.model_id,
            )
            uow.executions.clear_runtime_circuit(
                spec.runtime_type,
                spec.provider_id,
                None,
            )
