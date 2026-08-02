"""Build the ProviderCapacityPolicy from the config store.

Config keys (scope 'orchestrator'), all optional — an unset key keeps the app
layer's default, so behavior is unchanged until an operator deliberately tunes it:

  execution.provider_outage_ceiling_seconds       float (default 21600 = 6h)
  execution.provider_daily_quota_ceiling_seconds  float (default 93600 = 26h)
  execution.provider_max_inflight                 int   (default 8)
  execution.provider_probe_stale_after_seconds    float (default 900)

`provider_max_inflight` is only the GLOBAL FALLBACK cap: a provider row's
`max_inflight`, and a model row's override of it, both win over this (domain
unfreeze #16) — a paid tier, a free aggregator, and a local single-GPU server
need wildly different ceilings.

The ceilings bound how long a provider capacity outage is ridden out on automatic
waiting before it escalates to a human-gated block. They are WALL-CLOCK bounds,
deliberately not attempt counts: an outage's duration is what distinguishes "the
provider is busy" from "this configuration will never work".

Raise them to be more patient, lower them to be told sooner. The daily-quota
ceiling must stay above a real daily reset (>24h) or a free-tier allowance will
escalate every day instead of resuming on its own.

Read fresh per access like `build_retry_policy`, so `orchestrate config set`
applies without an API restart.
"""

from __future__ import annotations

from agent_orchestrator.app.provider_capacity import ProviderCapacityPolicy, RoutingPolicy, resolve_max_inflight
from agent_orchestrator.infra.db.reference_repos import SqliteConfigStore

_SCOPE = SqliteConfigStore.ORCHESTRATOR_SCOPE
_DEFAULTS = ProviderCapacityPolicy()
_ROUTING_DEFAULTS = RoutingPolicy()


def build_provider_capacity_policy(config_store: SqliteConfigStore) -> ProviderCapacityPolicy:
    return ProviderCapacityPolicy(
        outage_ceiling_seconds=float(
            config_store.get(_SCOPE, "execution.provider_outage_ceiling_seconds")
            or _DEFAULTS.outage_ceiling_seconds
        ),
        daily_quota_ceiling_seconds=float(
            config_store.get(_SCOPE, "execution.provider_daily_quota_ceiling_seconds")
            or _DEFAULTS.daily_quota_ceiling_seconds
        ),
        # Routed through `resolve_max_inflight` rather than `or`: the store
        # returns strings, and `"0"` is truthy, so the fallback below never fired
        # for the one value that stops every attempt from starting.
        max_inflight=resolve_max_inflight(
            model_cap=None,
            provider_cap=None,
            default=int(
                config_store.get(_SCOPE, "execution.provider_max_inflight")
                or _DEFAULTS.max_inflight
            ),
        ),
        probe_stale_after_seconds=float(
            config_store.get(_SCOPE, "execution.provider_probe_stale_after_seconds")
            or _DEFAULTS.probe_stale_after_seconds
        ),
    )


def build_routing_policy(config_store: SqliteConfigStore) -> RoutingPolicy:
    """Config keys (scope 'orchestrator'), all optional:

      execution.model_role_order               csv   (default "smart,long_context,cheap")
      execution.model_downgrade_after_seconds  float (default 60)
      execution.model_downgrade_floor_role     str   (default unset = any tier)

    These decide when a throttled provider is worth routing AROUND rather than
    waiting on. Preference is primary: on a paid setup a short 429 should be waited
    out rather than answered by silently running the work on a weaker model, so
    raise `model_downgrade_after_seconds` (or set a floor role) to bias toward
    waiting, and lower it to bias toward throughput on interchangeable free models.
    """
    raw_order = config_store.get(_SCOPE, "execution.model_role_order")
    order = (
        tuple(part.strip() for part in raw_order.split(",") if part.strip())
        if raw_order
        else _ROUTING_DEFAULTS.model_role_order
    )
    return RoutingPolicy(
        model_role_order=order or _ROUTING_DEFAULTS.model_role_order,
        downgrade_after_seconds=float(
            config_store.get(_SCOPE, "execution.model_downgrade_after_seconds")
            or _ROUTING_DEFAULTS.downgrade_after_seconds
        ),
        downgrade_floor_role=(
            config_store.get(_SCOPE, "execution.model_downgrade_floor_role")
            or _ROUTING_DEFAULTS.downgrade_floor_role
        ),
    )
