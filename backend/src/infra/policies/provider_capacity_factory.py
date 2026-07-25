"""Build the ProviderCapacityPolicy from the config store.

Config keys (scope 'orchestrator'), all optional — an unset key keeps the app
layer's default, so behavior is unchanged until an operator deliberately tunes it:

  execution.provider_outage_ceiling_seconds       float (default 21600 = 6h)
  execution.provider_daily_quota_ceiling_seconds  float (default 93600 = 26h)

These bound how long a provider capacity outage is ridden out on automatic
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

from src.app.provider_capacity import ProviderCapacityPolicy
from src.infra.db.reference_repos import SqliteConfigStore

_SCOPE = SqliteConfigStore.ORCHESTRATOR_SCOPE
_DEFAULTS = ProviderCapacityPolicy()


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
    )
