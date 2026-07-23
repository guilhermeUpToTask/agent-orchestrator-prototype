"""
src/infra/policies/retry_policy_factory.py — build the plan-level RetryPolicy
from the config store.

Config keys (scope 'orchestrator'), all optional — an unset key keeps the
domain's built-in default, so behavior is unchanged until an operator
deliberately tunes it:

  execution.retry_max_attempts             int    (default 3)
  execution.retry_initial_backoff_seconds  float  (default 30.0)
  execution.retry_backoff_multiplier       float  (default 2.0)
  execution.retry_max_backoff_seconds      float  (default 900.0)
  execution.retry_jitter_ratio             float  (default 0.2)

Called fresh at plan-creation time (never cached) so `orchestrate config set`
takes effect on the next created plan without an API restart — mirrors
reasoner.mode/agent_runner.mode's read-from-config-store pattern
(src/infra/reasoner/factory.py, src/infra/runtime/factory.py), but unlike
those two mode switches this has no invalid state to fail fast on: every key
is a plain int/float with a safe fallback.
"""

from __future__ import annotations

from src.domain.policies.retry_policies import RetryPolicy
from src.infra.db.reference_repos import SqliteConfigStore

_SCOPE = SqliteConfigStore.ORCHESTRATOR_SCOPE
_DEFAULTS = RetryPolicy()


def build_retry_policy(config_store: SqliteConfigStore) -> RetryPolicy:
    return RetryPolicy(
        max_attempts=int(
            config_store.get(_SCOPE, "execution.retry_max_attempts") or _DEFAULTS.max_attempts
        ),
        initial_backoff_seconds=float(
            config_store.get(_SCOPE, "execution.retry_initial_backoff_seconds")
            or _DEFAULTS.initial_backoff_seconds
        ),
        backoff_multiplier=float(
            config_store.get(_SCOPE, "execution.retry_backoff_multiplier")
            or _DEFAULTS.backoff_multiplier
        ),
        max_backoff_seconds=float(
            config_store.get(_SCOPE, "execution.retry_max_backoff_seconds")
            or _DEFAULTS.max_backoff_seconds
        ),
        jitter_ratio=float(
            config_store.get(_SCOPE, "execution.retry_jitter_ratio") or _DEFAULTS.jitter_ratio
        ),
    )
