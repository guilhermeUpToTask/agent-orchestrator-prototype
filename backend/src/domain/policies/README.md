# Policies

## `RetryPolicy` — the retry/backoff *decision*
Owns the **decision** (retry or go terminal? how long to back off?); the adapter owns the
**mechanism** (actually waiting, re-invoking). Persisted per-plan, so it's configurable and
survives crashes — which is what makes "retries exhausted → terminal FAILED" a domain rule
rather than adapter magic.

### `backoff_for(attempt)` — the `retry_index - 1` explained
`attempt` is the 1-based try count. Attempt 1 is the first try → no backoff (returns `0.0`).
`retry_index = attempt - 1` (attempt 2 → retry 1). The exponent is `retry_index - 1` so the
**first retry pays the base delay** (`multiplier ** 0 = 1×`), not an already-scaled one:

| attempt | retry_index | exponent | delay (initial=2, mult=2) |
|--------:|------------:|---------:|--------------------------:|
| 1       | —           | —        | `0.0` (first try)         |
| 2       | 1           | 0        | `2 * 2**0 = 2s`           |
| 3       | 2           | 1        | `2 * 2**1 = 4s`           |
| 4       | 3           | 2        | `2 * 2**2 = 8s`           |

Capped at `max_backoff_seconds`. Without the `- 1`, the first retry would jump straight to
`2 * 2**1 = 4s`, skipping the 2s base step.

### `non_retryable_reasons`
Currently exact-string-matches a free-form `failure_reason` (`["invalid_input"]`). That's
brittle and under-specified — see [`../../DESIGN_NOTES.md`](../../DESIGN_NOTES.md) (typed
`FailureKind` for retry classification).
