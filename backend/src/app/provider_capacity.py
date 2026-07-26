"""Provider capacity policy: which circuit a capacity failure belongs to.

A provider reports capacity exhaustion at two independent tiers, and conflating
them is what makes a capacity outage look like a plan-stopping fault:

*Account-level* (`QUOTA`, `DAILY_QUOTA`) — a spend cap, credit balance, or
free-tier daily allowance. Every model reachable through that key shares it, so
the circuit is PROVIDER-WIDE: routing to a different model does not escape it.

*Upstream-level* (`REQUEST_CONCURRENCY`, `UNKNOWN_CAPACITY`) — one inference
pool's in-flight ceiling. An aggregator routes each model to its own upstream, so
these are PER-MODEL: one saturated model must not throttle the others on the same
key. This is also correct for a direct paid API, where RPM/TPM ceilings are
per-model while billing is account-wide.

The mapping is a default, not a law. A single-endpoint provider (a self-hosted
NIM deployment, a local server) shares one pool across every model it serves, so
its concurrency limits are endpoint-wide too. That case is expressed by provider
metadata, NOT by branching on a provider name here -- see `CapacityScope`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from src.app.runtime_failures import LimitScope
from src.domain.value_objects.lifecycle import FailureKind

# Limit tiers that a whole account shares, regardless of which model was called.
_ACCOUNT_LEVEL_SCOPES = frozenset({LimitScope.QUOTA, LimitScope.DAILY_QUOTA})

# Failure kinds that mean "the provider has no room right now", as opposed to
# "this task cannot succeed". These are ridden out on automatic waiting bounded
# by wall-clock time; everything else keeps its ordinary per-task retry budget.
CAPACITY_KINDS = frozenset({FailureKind.RATE_LIMIT, FailureKind.CONNECTION_ERROR})


@dataclass(frozen=True)
class ProviderCapacityPolicy:
    """Operator-tunable bounds on how long capacity is waited out.

    The bound is WALL-CLOCK, not an attempt count. Attempt counts are the wrong
    unit for an outage: a provider-global failure counter compared against a
    per-task budget latched after a handful of shared failures while each task
    had barely retried, and with several goals in flight that arrived in minutes.

    Defaults ride out every legitimate outage (real ones last minutes to hours)
    while still escalating a misconfiguration that merely looks transient -- a
    revoked key returning 429, or a wrong base_url, produces the same signature
    forever, and without a backstop the root would report RUNNING and nobody
    would ever be told.
    """

    outage_ceiling_seconds: float = 21_600.0  # 6h
    daily_quota_ceiling_seconds: float = 93_600.0  # 26h — must exceed a daily reset
    # How long a half-open probe may be held before another runner may take it.
    # MUST exceed `agent_runner.timeout_seconds` (default 600): the probe is held
    # for the whole attempt, so a shorter bound would let a second runner steal it
    # mid-attempt and reintroduce the very herd single-flight exists to prevent.
    # This is why it is not the worker lease (60s) -- that is far shorter than an
    # attempt.
    probe_stale_after_seconds: float = 900.0
    # Global fallback in-flight cap, used when neither the model row nor the
    # provider row declares one. Deliberately conservative: exceeding a provider's
    # concurrency ceiling is what produces the refusals this design absorbs.
    max_inflight: int = 8
    # NOTE: the "quiet long enough to be a new outage" threshold is deliberately
    # NOT a separate knob — see `outage_start`. It is derived from the scope's own
    # ceiling, because any fixed value small enough to catch stale rows is also
    # small enough to fire during a legitimate wait. A 3600s constant collided
    # exactly with the daily-quota backoff floor, which would have stopped a daily
    # quota from ever escalating.

    def ceiling_for(self, limit_scope: str | None) -> float:
        """A daily allowance can legitimately take a full day to reset, so it gets
        the longer ceiling; every other tier uses the ordinary one."""
        if limit_scope == LimitScope.DAILY_QUOTA.value:
            return self.daily_quota_ceiling_seconds
        return self.outage_ceiling_seconds

    def outage_exceeded(self, opened_at: datetime, now: datetime, limit_scope: str | None) -> bool:
        return (now - opened_at).total_seconds() > self.ceiling_for(limit_scope)

    def outage_start(
        self,
        existing_opened_at: datetime | None,
        existing_retry_at: datetime | None,
        now: datetime,
        limit_scope: str | None = None,
    ) -> datetime:
        """The outage start to record for a failure happening `now`.

        Continues an in-progress outage, but starts a FRESH window when the prior
        circuit has been quiet for longer than this scope's own ceiling. Without
        that reset, a circuit row abandoned by an earlier session pre-ages the first
        failure of a new outage past the ceiling and escalates it immediately — the
        precise premature escalation this design exists to remove. A circuit is
        normally deleted by the first success, but nothing deletes one when the work
        simply stops being attempted, so stale rows are routine.

        The threshold is the LONGEST ceiling, not this scope's own, and not a
        constant. It has to exceed anything the policy would ever legitimately wait,
        because reaching a ceiling necessarily involves a long wait — using the
        scope's own ceiling made the rule contradict escalation itself (an outage
        old enough to escalate always looked quiet enough to reset), and a 3600s
        constant collided with the daily-quota backoff floor.

        Quiet beyond even a daily reset is genuinely ambiguous — the provider may
        have recovered unobserved, or nothing retried. Resetting resolves that
        toward PATIENCE, since escalating early is the failure mode that hurts.
        """
        if existing_opened_at is None:
            return now
        quiet_threshold = max(self.daily_quota_ceiling_seconds, self.outage_ceiling_seconds)
        if (
            existing_retry_at is not None
            and (now - existing_retry_at).total_seconds() > quiet_threshold
        ):
            return now
        return existing_opened_at


@dataclass(frozen=True)
class RoutingPolicy:
    """When it is worth running a task on a different model than the bound one.

    PREFERENCE BEATS AVAILABILITY. Routing around a throttled model is a win on a
    free tier, where waits are long and models are interchangeable, and a
    regression on a paid one, where substituting a weaker model to dodge a
    ten-second 429 buys nothing and costs output quality. So the bound agent's
    tier is primary and availability is only the tiebreak.
    """

    # Highest-preference model tier first. These are `AgentSpec.model_role`
    # values, which already exist as a model *tier* indirection. Unlisted roles
    # sort last.
    model_role_order: tuple[str, ...] = ("smart", "long_context", "cheap")
    # Substitute for a CIRCUIT wait only when it exceeds this. A short 429 is
    # waited out. (An at-capacity provider substitutes immediately instead —
    # there is no wait to compare against, and applying a threshold there would
    # make the admission gate serialize the work it exists to parallelize.)
    downgrade_after_seconds: float = 60.0
    # Never substitute below this tier. Unset = any tier is acceptable.
    downgrade_floor_role: str | None = None

    def tier_rank(self, model_role: str) -> int:
        try:
            return self.model_role_order.index(model_role)
        except ValueError:
            return len(self.model_role_order)

    def allows(self, model_role: str) -> bool:
        if self.downgrade_floor_role is None:
            return True
        return self.tier_rank(model_role) <= self.tier_rank(self.downgrade_floor_role)


def resolve_capacity_scope(raw: str | None) -> CapacityScope:
    """A provider row's declared scope, tolerating unset and unrecognized values.

    An unknown string falls back to PER_MODEL rather than raising: a typo in
    operator metadata must not take execution down, and PER_MODEL is the safe
    default (it never lets one model's limit throttle its siblings)."""
    if raw is None:
        return CapacityScope.PER_MODEL
    try:
        return CapacityScope(raw)
    except ValueError:
        return CapacityScope.PER_MODEL


class CapacityScope(str, Enum):
    """How a provider's capacity limits are structured.

    PER_MODEL — account-level limits are shared, upstream limits are per routed
    model. Correct for aggregators (OpenRouter) and direct paid APIs.

    ENDPOINT_WIDE — one pool serves every model, so upstream limits are shared
    too. Correct for a self-hosted or single-endpoint deployment.
    """

    PER_MODEL = "per_model"
    ENDPOINT_WIDE = "endpoint_wide"


def circuit_model_id(
    model_id: str | None,
    limit_scope: LimitScope | None,
    capacity_scope: CapacityScope = CapacityScope.PER_MODEL,
) -> str | None:
    """The `model_id` component of the circuit key for this failure.

    `None` means a provider-wide circuit. An unclassified scope
    (`UNKNOWN_CAPACITY` or `None`) is treated as upstream-level: the narrower
    key. Guessing "account-wide" from an unrecognized message would let one
    model's transient blip halt every other model on the key, so an unmatched
    provider message degrades to the coarse-but-safe option, never the harmful
    one.
    """
    if limit_scope is not None and limit_scope in _ACCOUNT_LEVEL_SCOPES:
        return None
    if capacity_scope is CapacityScope.ENDPOINT_WIDE:
        return None
    return model_id


# A provider_capacity block carries the circuit it came from as an evidence ref so
# `wait_and_retry` can clear exactly that circuit. A provider-wide circuit has no
# model component, and the URI has three positional segments, so it needs a
# stand-in token. Build and parse refs ONLY through this pair -- the round trip is
# what keeps block resolution able to find the row the block was opened for.
_PROVIDER_WIDE_REF = "_provider_wide"
_CIRCUIT_REF_PREFIX = "runtime-circuit://"


def circuit_ref(runtime: str, provider_id: str, model_id: str | None) -> str:
    model = _PROVIDER_WIDE_REF if model_id is None else model_id
    return f"{_CIRCUIT_REF_PREFIX}{runtime}/{provider_id}/{model}"


def parse_circuit_ref(ref: str) -> tuple[str, str, str | None]:
    """Inverse of `circuit_ref`. Raises ValueError on a malformed ref; callers
    translate that into their own domain error."""
    runtime, provider_id, model = ref.removeprefix(_CIRCUIT_REF_PREFIX).split("/", 2)
    return runtime, provider_id, (None if model == _PROVIDER_WIDE_REF else model)
