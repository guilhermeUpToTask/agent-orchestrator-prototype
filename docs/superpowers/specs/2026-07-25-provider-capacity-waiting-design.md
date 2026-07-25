# Provider capacity as waiting, not blocking — design

- **Date**: 2026-07-25
- **Status**: IMPLEMENTED (see §11 for where the build corrected this design)
- **Scope**: `src/app`, `src/infra`, `src/api`, `alembic`, plus **domain un-freeze #16**
  (decision 56) for provider capacity metadata — see §11
- **Supersedes**: nothing. Related roadmap item: architect/enrichment redesign (ROADMAP item 35) is independent.

## 1. Problem

A provider capacity outage currently ends a plan's forward progress by opening a
human-gated block, on two independent paths.

**Execution.** `ExecutionHandler._finalize_failure` latches the runtime circuit's
`manual_intervention` flag at `backend/src/app/handlers/execution_handler.py:1242-1244`:

```python
kind_budget = unit.retry_policy.kind_max_attempts.get(exc.kind, unit.retry_policy.max_attempts)
circuit_manual = failure_count >= kind_budget
```

`failure_count` is a **provider-global cumulative** counter; `kind_budget` is a
**per-task attempt** budget. The two are different units of measure. The comparison
also bypasses the normalization `RetryPolicy.should_retry` deliberately applies at
`backend/src/domain/policies/retry_policies.py:45-54`:

```python
budget = max(self.kind_max_attempts.get(kind, self.max_attempts), self.max_attempts)
```

whose own comment states that `max_attempts` is operator-configured *precisely so a
provider capacity outage can be ridden out on automatic backoff instead of opening a
human-gated block*. The latch contradicts that intent: raising
`execution.retry_max_attempts` extends per-task retries but the circuit still latches
at the hardcoded per-kind default of 6. With `max_concurrent_goals=4` the global
counter reaches 6 after roughly two outage windows while each individual task has
used one or two attempts. The existing regression test
`backend/tests/unit/test_retry_policy.py:43`
(`test_operator_raised_max_attempts_is_never_shadowed_by_a_kind_budget`) already locks
the policy-object invariant that this line violates.

**Planning.** `PlanningHandler._handle_reasoner_failure` decides terminality at
`backend/src/app/handlers/planning_handler.py:439`:

```python
terminal = not exc.transient or next_attempt >= plan.retry_policy.max_attempts
```

`ReasonerUnavailable` (`backend/src/app/ports.py:122-136`) carries only
`transient: bool` — no `FailureKind`, no `retry_after`. Three consecutive transient
failures therefore open a `reasoner_failure` block regardless of cause. Because
`provider_error_from_empty_choices`
(`backend/src/infra/reasoner/runtime/errors.py:63-79`) is unconditionally
`transient=True` and fires on `HTTP 200` with no `choices` — how OpenRouter surfaces
out-of-credits and rate-limited responses — routine throttling reaches this line
indistinguishable from any other transient fault. `backoff_for` is also called with
`kind=None` (`planning_handler.py:498`), so planning caps at
`max_backoff_seconds` (900s) and never gets the daily-quota floor the execution path
applies at `execution_handler.py:1223-1228`.

**Concurrency.** Four in-process goal workers (`backend/src/infra/worker/main.py:208-215`)
each run `_run_goal` on their own UnitOfWork, so the circuit check at
`execution_handler.py:196` fires four times in parallel. The half-open probe at
`execution_handler.py:1099-1105` documents that "one invocation may proceed" but
nothing enforces it — one outage window costs four failures. Separately,
`_limit_scope` (`backend/src/infra/runtime/taxonomy.py:77-85`) detects
`REQUEST_CONCURRENCY` only from the substrings `"concurr"` and `"simultaneous"`.
NVIDIA's wording, relayed through OpenRouter as
`"Upstream error from Nvidia: ResourceExhausted: Worker local total request limit reached (33/32)"`,
matches neither and falls through to `UNKNOWN_CAPACITY`.

## 2. Goals and non-goals

**Goals.** Transient provider capacity becomes *automatic waiting*, bounded in
wall-clock time rather than attempt count. Concurrency limits reduce in-flight work
rather than halting a provider. Blocks are reserved for failures that require a human
decision. Parallel goals route around a throttled model when a suitable alternative is
registered.

**Non-goals.** No lifecycle redesign. No new `PlanStatus` value. No change to the
cyclic `ProjectPlan` model, gates, or dispositions. No forge/PR work. No Redis. Cost
or spend accounting is out of scope. The architect/enrichment redesign (ROADMAP 35) is
untouched.

## 3. Principles

1. **Blocks are for decisions.** A block means a human must choose something:
   authentication or invalid credentials, unsatisfiable capabilities, context/token
   limit requiring task changes, verification failure, merge conflict, invalid
   contract, explicit provider/model removal. Capacity is not a decision.
2. **Bound the wait in wall-clock, not attempts.** Attempt counts are the wrong unit
   for an outage. A revoked key returning 429, or a wrong `base_url`, produces the same
   transient signature indefinitely; without a time backstop the root reports RUNNING
   forever and nobody is told.
3. **Provider capacity facts are catalog data.** In-flight caps and limit-tier
   structure live on the provider/model rows. No handler contains
   `if provider == "openrouter"`.
4. **Scope detection refines behavior; it must never be required for correctness.**
   `_limit_scope` is a regex heuristic over provider prose. `UNKNOWN_CAPACITY` stays the
   default and the generic path stays safe, so an unrecognized message degrades to
   coarse-but-correct, never to broken.
5. **Provider prose stays in infrastructure.** Pattern matching lives in
   `src/infra/runtime/taxonomy.py`. Only `FailureKind` and `LimitScope` cross into
   `app`/`domain`; no provider name ever does.
6. **Preference beats availability.** Routing around a throttled model is a win on a
   free tier and a regression on a paid one. Tier order is primary; availability is the
   tiebreak.

## 4. Items

### Item 1 — single-flight half-open probe

**Current.** `_runtime_circuit_signal` (`execution_handler.py:1053-1105`) returns
`NOT_READY` while `now < circuit.retry_at`, then returns `None` to let a probe through.
Every concurrent goal worker that checks after `retry_at` gets `None`.

**Change.** Add `probe_holder: str | None` and `probe_started_at: datetime | None` to
`RuntimeCircuit` (`backend/src/app/execution_records.py:105-115`). After `retry_at`,
the checker attempts to claim the probe with a conditional UPDATE inside the claim
transaction — `SET probe_holder = :run_id WHERE probe_holder IS NULL OR probe_started_at < :stale_cutoff`.
SQLite write serialization makes exactly one claimant. The winner proceeds; losers
return `NOT_READY`. A probe whose holder died is released once
`probe_started_at` is older than the worker's configured `lease_seconds` (the same
value the plan lease uses, passed down from `worker start`), mirroring the plan-lease
expiry pattern rather than introducing a second tunable. Every success finalizer's
`_clear_runtime_circuit` (`execution_handler.py:1126-1140`) already deletes the row,
which clears the probe with it; the failure path releases `probe_holder` back to `NULL`
when it rewrites `retry_at`.

**Rationale.** Restores the invariant the existing docstring claims and removes the
4× failure inflation per outage window.

### Item 2 — wall-clock ceiling replaces the attempt latch

**Current.** `execution_handler.py:1242-1244`, as quoted in §1.

**Change.** Delete the `failure_count >= kind_budget` comparison. `failure_count`
survives for backoff growth and telemetry. Latch `manual_intervention` only when

```
now - circuit.opened_at > ceiling
```

`opened_at` is already persisted (`execution_records.py:110`,
`backend/src/infra/db/tables.py:284`), so the ceiling itself needs no schema change.
`ceiling` is selected by `limit_scope`: the daily-quota ceiling when
`limit_scope == DAILY_QUOTA`, otherwise the default ceiling.

**Config** (scope `orchestrator`, resolved in
`backend/src/infra/policies/retry_policy_factory.py` beside the existing
`execution.retry_*` keys):

| key | default | meaning |
| --- | --- | --- |
| `execution.provider_outage_ceiling_seconds` | `21600` (6h) | ordinary capacity/connection outage |
| `execution.provider_daily_quota_ceiling_seconds` | `93600` (26h) | `limit_scope == daily_quota`; must exceed a daily reset |

**Rationale.** Real outages last minutes to hours, so a 6h ceiling rides out every
legitimate one; a daily reset needs >24h. A misconfiguration that merely looks
transient still escalates in hours instead of never.

### Item 3 — persist `limit_scope`; extend circuits to `CONNECTION_ERROR`

**Current.** The circuit-open branch at `execution_handler.py:1230-1258` is gated on
`exc.kind == FailureKind.RATE_LIMIT`. `CONNECTION_ERROR` never opens a circuit; it
exhausts the per-task budget (`kind_max_attempts` default 5,
`retry_policies.py:24-25`) and reaches `fail_task`, which opens a goal block. The
circuit row does not persist `limit_scope`, so items 2 and 4 cannot read it back.

**Change.** Add `limit_scope: str | None` to `RuntimeCircuit` and the
`runtime_circuits` table, written from `exc.failure.limit_scope`. Widen the
circuit-open condition to `exc.kind in {RATE_LIMIT, CONNECTION_ERROR}`.

### Item 4 — scope-aware classification and circuit key

**Current.** `_limit_scope` (`taxonomy.py:77-85`) checks `"concurr"` / `"simultaneous"`
for `REQUEST_CONCURRENCY`; `"per day"` / `"daily"` / `"free-models-per-day"` for
`DAILY_QUOTA`; `"quota"` / `"credit"` for `QUOTA`. The circuit key is always
`(runtime, provider_id, model_id)` (`execution_handler.py:1237-1241`).

**Change, part A.** Add `"request limit reached"`, `"total request limit"`, and
`"too many requests"` to the `REQUEST_CONCURRENCY` branch. Ordering matters: the daily
checks stay first so `"free-models-per-day"` is not shadowed.

**Change, part B.** The circuit key follows the provider's declared `capacity_scope`:

| `capacity_scope` | `QUOTA` / `DAILY_QUOTA` | `REQUEST_CONCURRENCY` / `UNKNOWN_CAPACITY` |
| --- | --- | --- |
| `per_model` (default) | `(runtime, provider_id)` | `(runtime, provider_id, model_id)` |
| `endpoint_wide` | `(runtime, provider_id)` | `(runtime, provider_id)` |

`per_model` is correct for aggregators (OpenRouter fans out to distinct upstream pools
per routed model, while billing and daily caps are account-wide) and for direct paid
APIs (per-model RPM/TPM tiers, account-wide spend). `endpoint_wide` is for a
self-hosted or single-endpoint provider whose worker pool is shared across models —
a direct NVIDIA NIM deployment or a local server. Because the key is now sometimes
model-agnostic, the repository's circuit accessors take `model_id: str | None`, and
`NULL` model means a provider-wide row.

**Rationale.** Keying concurrency provider-wide would let one saturated model throttle
every other model on the same key. Keying quota per-model would open N independent
circuits over one exhausted account. The structure is a provider fact, so it is
provider data.

### Item 5 — `REQUEST_CONCURRENCY` does not halt the provider

**Change.** When `limit_scope == REQUEST_CONCURRENCY`, requeue that single task with a
short backoff (`backoff_for` with the existing `RATE_LIMIT` curve, no daily floor) and
**do not** write a circuit row. In-flight siblings continue untouched. The failure is
still recorded on the attempt for telemetry, as with every other kind.

**Rationale.** Quota exhaustion means *stop for a while*; a concurrency cap means *send
fewer at once*. Opening a circuit for the 33rd request halts a provider that was
serving 32 successfully.

### Item 6 — provider admission gate

**Current.** Nothing limits how many attempts run concurrently against one provider.
`ExecutionRun` (`execution_records.py:35-43`) carries no `provider_id`/`model_id` —
those live on `ExecutionAttempt` (`:46-71`) — and no repository method counts running
work. `list_open_attempts(plan_id)` is plan-scoped.

**Change.** New repository method on the `ExecutionRecordRepository` Protocol
(`execution_records.py:118-170`), implemented in both
`backend/src/infra/db/execution_record_repository.py` and the fake at
`backend/src/app/testing/execution_records.py`:

```python
def count_inflight_attempts(
    self, runtime: str, provider_id: str, model_id: str | None
) -> int: ...
```

It counts attempts with no `completed_at`, **across all plans** — an upstream pool is
shared by every running plan, so a plan-scoped count would let two plans each open a
full cap's worth. `model_id=None` counts provider-wide.

In the claim transaction, before `_start_unit` (`execution_handler.py:953`) and after
`_resolve_spec`, compare the count against the effective cap; at or above it, return
`NOT_READY`. Cap resolution order: model row override → provider row → global config
key `execution.provider_max_inflight` (default `8`).

**Rationale.** Converts "fire the 33rd request, fail, back off" into "never fire the
33rd". Combined with item 1 this largely dissolves the herd at the source. Making the
cap provider data rather than one global number is load-bearing: a single value would
throttle a paid tier to free-tier levels, or over-drive a local single-GPU model.

### Item 7 — kind-aware planning terminality and a waiting surface

**Change, part A.** `ReasonerUnavailable` (`ports.py:122-136`) gains
`kind: FailureKind | None = None` and `retry_after_seconds: float | None = None`.
`FailureKind` already lives in the domain, so this adds no new dependency and stays
app-side.

**Change, part B.** Every raise site supplies a kind. Mapping table:

| site | current | kind |
| --- | --- | --- |
| `infra/reasoner/runtime/errors.py:63-79` `provider_error_from_empty_choices` | `transient=True` | `RATE_LIMIT` when the body names a rate/quota/credit condition, else `CONNECTION_ERROR` |
| `errors.py:46-51` `classify_provider_error`, generic provider/network/timeout | `transient=True` | `TIMEOUT` on a timeout type, else `CONNECTION_ERROR`; `RATE_LIMIT` on HTTP 429 |
| `errors.py:57` `classify_provider_error`, 404 or "tool use" in text | `transient=False` | `TOOL_ERROR` |
| `infra/reasoner/runtime/agent_loop.py:79` plain reply where a submit was required | `transient=True` | `TOOL_ERROR` |
| `agent_loop.py:117` `max_turns` exhausted without submitting | `transient=True` | `TOOL_ERROR` |
| `infra/reasoner/openai_reasoner.py:92` submission fails Pydantic validation | `transient=True` | `TOOL_ERROR` |

Reuse `classify_failure` / `_limit_scope` from `taxonomy.py` for the message-derived
cases rather than writing a second classifier.

**Change, part C.** `planning_handler.py:439` becomes kind-driven. Capacity kinds
(`RATE_LIMIT`, `CONNECTION_ERROR`, `TIMEOUT`) keep arming the durable backoff gate
under the same wall-clock ceiling as item 2. The ceiling needs an outage *start* time,
which the aggregate does not carry: `Plan` has `planning_retry_not_before` and
`planning_attempts` but no first-arm timestamp, and reconstructing one from the attempt
count and the backoff curve would be guesswork. Instead the handler reads it from the
planning-operation ledger — the earliest `BACKING_OFF` operation for the current stage
since the last committed one, via the existing `list_planning_operations`
(`execution_records.py:162`). That is execution-record state the handler already
touches, so **no new domain field is required**. `TOOL_ERROR`, `TOKEN_LIMIT`, and `AUTH_ERROR`
remain terminal at the existing `max_attempts`. `backoff_for` at
`planning_handler.py:498` receives the real kind and honors
`retry_after_seconds`, plus the daily-quota floor already used at
`execution_handler.py:1223-1228`.

**Rationale.** `agent_loop.py:79` and `:117` are `transient=True` today but describe a
model that structurally cannot do tool calls. Backing those off forever would loop
against a model that will never succeed. Gating on kind rather than on the boolean is
what makes indefinite capacity waiting safe.

**Change, part D — retry-layer composition.** `llm_client.py:150` already runs its own
`_max_retries` loop before raising. The handler-level gate must not multiply it: the
client owns short in-call retries (seconds, for a single provider blip) and re-raises
with the classified kind; the handler owns the durable cross-tick gate (tens of seconds
upward). The spec fixes the client's ceiling below the handler's
`initial_backoff_seconds` so the two are visibly separate tiers, and the handler's
attempt counter counts *handler* attempts, never client sub-attempts.

**Change, part E — the waiting surface.** `status_reason` and `legal_actions` are pure
`Plan` properties (`backend/src/domain/aggregates/planner_orchestrator.py:733-783` and
`:792-838`) reading only in-aggregate state, assigned straight through at
`backend/src/api/routers/plans.py:542` and `:547`. The aggregate cannot see a
`RuntimeCircuit`, so the circuit is **not** folded into `status_reason`. Instead
`PlanDetailResponse` gains a sibling field:

```python
provider_waiting: ProviderWaiting | None  # scope, retry_at, since, safe_message
```

built in `get_plan` from `get_runtime_circuit`, exactly as `active_run` and
`planning_progress` are already built from execution-record state at
`plans.py:491-533`. A non-blocking `ProviderWaiting` outbox event is emitted when a
circuit first opens, so the SSE feed shows the wait. The root stays `RUNNING`: it is
running, merely unclaimable this tick.

### Item 8 — tier-ordered, throttle-aware selection

**Current.** `plan.bind_agents(agents, default_id)`
(`planner_orchestrator.py:1151-1164`) binds each task once at planning time via the
pure `match_agent` (`backend/src/domain/services/capability_matching.py:18-29`), which
returns the *first* agent whose capabilities cover the requirement. At execution,
`_resolve_spec` (`execution_handler.py:1038-1051`) resolves the bound
`role_agent_ids[run_role]` or `task.agent_id` to an `AgentSpec`. Selection is therefore
capability-driven and fixed; four equally-capable agents all bind to the same one.

**Change.** Selection becomes a *runtime substitution* at spec-resolution time, not a
re-binding. `_resolve_spec` continues to yield the **preferred** spec from the
persisted binding. A new app-layer step then chooses the spec actually used:

1. Candidates = registry agents whose `capabilities` cover the task's
   `required_capabilities` and whose `role` matches the resolved `run_role`.
2. Order by `AgentSpec.model_role` tier, preferred agent first. `model_role` already
   exists and is documented as a model *tier* indirection
   (`backend/src/domain/entities/agent_spec.py:13-16`), so no new field is needed. Tier
   order comes from config key `execution.model_role_order` (default
   `smart,long_context,cheap`); unlisted roles sort last.
3. Keep the preferred spec unless it is unavailable. The two unavailability causes are
   deliberately treated differently:
   - **Circuit open** — substitute only when the projected wait
     `circuit.retry_at - now` exceeds `execution.model_downgrade_after_seconds`
     (default `60`). A short 429 is waited out.
   - **At its in-flight cap** — substitute **immediately**, with no threshold. There is
     no wait to compare against: the cap says this pool is saturated right now, while
     another pool is idle. Applying the time threshold here would make the admission
     gate serialize work it was added to parallelize.
4. Substitute the highest-tier available candidate. If none is available, return
   `NOT_READY` and wait — never downgrade below the configured floor
   `execution.model_downgrade_floor_role` (unset by default, meaning any tier).

The task's persisted `agent_id` / `role_agent_ids` are **never mutated** — that would
violate the aggregate's ownership of task fields and would rewrite the recorded
preference. What actually ran is recorded on `ExecutionAttempt`, which already carries
`provider_id` and `model_id`, so every attempt remains auditable.

The candidate filter is implemented app-side against `AgentSpec.capabilities` rather
than by adding a plural `matching_agent_ids` to the domain service, keeping this item
clear of any un-freeze discussion.

**Rationale.** On free tiers, waits are long and tiers are flat, so substitution
happens immediately and four goals genuinely run four-wide across distinct upstream
pools. On a paid setup, a short 429 is waited out rather than answered by degrading to
a weaker model.

## 5. Schema — migration `0012`

Chain head is `0011_goal_leases`, so this is `0012_provider_capacity`.

**`runtime_circuits`** (`tables.py:279-295`): add `limit_scope TEXT NULL`,
`probe_holder TEXT NULL`, `probe_started_at TEXT NULL`. The composite primary key
`(runtime, provider_id, model_id)` must tolerate a provider-wide row; since SQLite
primary-key columns are `NOT NULL`, provider-wide rows use the sentinel `model_id = '*'`
rather than `NULL`, and the repository translates `model_id=None` to the sentinel at
its boundary. This keeps the sentinel out of every caller.

**`providers`** (`tables.py:415-422`): add `max_inflight INTEGER NULL`,
`capacity_scope TEXT NULL` (`per_model` | `endpoint_wide`; `NULL` means `per_model`).

**`models`** (`tables.py:425-432`): add `max_inflight INTEGER NULL` — a per-model
override of the provider value.

All columns nullable with no server default beyond `NULL`, so existing rows migrate
untouched and the global config keys remain the fallback. Downgrade drops the columns.

## 6. Config keys (scope `orchestrator`)

| key | default | item |
| --- | --- | --- |
| `execution.provider_outage_ceiling_seconds` | `21600` | 2 |
| `execution.provider_daily_quota_ceiling_seconds` | `93600` | 2 |
| `execution.provider_max_inflight` | `8` | 6 |
| `execution.model_role_order` | `smart,long_context,cheap` | 8 |
| `execution.model_downgrade_after_seconds` | `60` | 8 |
| `execution.model_downgrade_floor_role` | unset | 8 |

Existing `execution.retry_*` keys are unchanged. `orchestrate seed demo` seeds the new
keys idempotently, consistent with how it seeds the reasoner and runner keys.

## 7. Test matrix

New tests live in `backend/tests/unit/orchestration/` so they run through the
parametrized `env_factory` — in-memory fakes **and** the real SQLite UnitOfWork.

| item | test | asserts |
| --- | --- | --- |
| 1 | `test_half_open_probe_is_single_flight` | four concurrent goal workers past `retry_at` produce exactly one probe; the other three get `NOT_READY` |
| 1 | `test_stale_probe_is_reclaimed_after_holder_death` | a probe older than the stale cutoff is reclaimable |
| 2 | `test_rate_limit_storm_inside_ceiling_never_latches` | 20 rate limits within the ceiling leave `manual_intervention` false and the plan RUNNING |
| 2 | `test_outage_past_ceiling_latches_manual_intervention` | `FakeClock.advance()` past the ceiling latches and opens `provider_capacity` |
| 2 | `test_daily_quota_uses_the_longer_ceiling` | `limit_scope=daily_quota` does not latch at the default ceiling |
| 2 | `test_operator_raised_max_attempts_is_not_shadowed_by_the_circuit` | the item-2 counterpart to `tests/unit/test_retry_policy.py:43` |
| 3 | `test_connection_error_opens_a_circuit_and_waits` | a connection storm waits instead of reaching `fail_task` |
| 4 | `test_nvidia_worker_limit_classifies_as_request_concurrency` | the literal `(33/32)` message maps to `REQUEST_CONCURRENCY` (unit test in `tests/unit/runtime/`) |
| 4 | `test_free_models_per_day_still_classifies_as_daily_quota` | daily detection is not shadowed by the new patterns |
| 4 | `test_quota_circuit_is_provider_wide_concurrency_is_per_model` | two models on one provider share a quota circuit, not a concurrency circuit |
| 4 | `test_endpoint_wide_provider_shares_a_concurrency_circuit` | `capacity_scope=endpoint_wide` keys concurrency provider-wide |
| 5 | `test_request_concurrency_requeues_without_opening_a_circuit` | siblings keep running; no circuit row exists |
| 6 | `test_admission_gate_caps_inflight_per_provider_model` | the N+1st claim returns `NOT_READY` |
| 6 | `test_admission_gate_counts_across_plans` | two plans cannot each open a full cap |
| 6 | `test_model_override_beats_provider_cap_beats_global` | cap resolution order |
| 7 | `test_transient_rate_limit_keeps_backing_off_past_max_attempts` | no `reasoner_failure` block while inside the ceiling |
| 7 | `test_tool_error_still_blocks_at_max_attempts` | `agent_loop`-style failures stay terminal |
| 7 | `test_planning_backoff_honors_retry_after_and_daily_floor` | delay respects both |
| 7 | `test_provider_waiting_is_served_without_touching_status_reason` | the sibling field is populated; `status_reason` is byte-identical to the aggregate property (API test) |
| 8 | `test_selection_prefers_bound_agent_when_available` | no substitution when nothing is throttled |
| 8 | `test_selection_substitutes_only_past_the_downgrade_threshold` | a 10s circuit wait does not downgrade; a 10min wait does |
| 8 | `test_selection_substitutes_immediately_when_at_inflight_cap` | a saturated cap substitutes with no threshold delay |
| 8 | `test_selection_never_mutates_the_persisted_binding` | `task.agent_id` unchanged; the attempt records the substitute |
| 8 | `test_selection_waits_when_every_candidate_is_throttled` | `NOT_READY`, no block |

**Existing tests that must be updated, not deleted** (from the recon inventory):

- `tests/unit/orchestration/test_execution_records.py:209`
  `test_provider_circuit_blocks_head_goal_without_running_later_task` — asserts
  `manual_intervention` ratcheting on the old attempt-count rule. Rewrite to drive the
  clock past the ceiling; the *block* assertion at `:291` stays valid.
- `tests/unit/orchestration/test_reasoner_backoff.py:72`
  `test_transient_failures_exhaust_budget_then_fail` — encodes exactly the behavior item
  7 changes. Re-point at a `TOOL_ERROR` kind so it keeps guarding terminality, and add
  the capacity-kind counterpart.
- `tests/integration/test_default_cyclic_execution.py:202` and
  `tests/unit/orchestration/test_pause_resume.py:319` — seed circuits via
  `upsert_runtime_circuit`; both need the new fields. Behavior unchanged.
- `tests/integration/test_plan_run_export.py:395` and
  `tests/integration/test_block_report.py:118` — SQL `_seed_database` helpers list
  circuit columns explicitly; add the new ones.
- `tests/unit/reasoner/test_llm_client.py`, `test_agent_loop.py`,
  `test_openai_reasoner.py` — assert on `.transient`; extend to assert `.kind`.

`mypy src` must pass with zero errors and no new excludes. `ruff check src tests`
clean.

## 8. Commit order

Each commit is independently green and independently revertable.

1. `0012` migration + `RuntimeCircuit`/provider/model column plumbing including
   persisting `limit_scope` (real repo **and** the fake in lockstep) — item 3, schema
   half only. No behavior change.
2. `_limit_scope` patterns + scope-aware circuit key — item 4.
3. `REQUEST_CONCURRENCY` requeue-without-circuit — item 5.
4. Wall-clock ceiling replacing the attempt latch — item 2.
5. **Extend circuits to `CONNECTION_ERROR`** — item 3, behavior half. This must land
   *after* commit 4, not with commit 1: opening connection circuits while the
   attempt-count latch is still in place would latch `manual_intervention` after five
   shared failures, making the interim commit worse than the status quo.
6. Single-flight probe — item 1.
7. Admission gate — item 6. **The parallelism walkthrough is testable here.**
8. `ReasonerUnavailable` kind/retry_after + planning terminality + `provider_waiting`
   surface — item 7.
9. Tier-ordered throttle-aware selection — item 8.

## 9. Division of labor

Opus drives items 1, 2, 5, 6, 8 and audits the whole diff. Sonnet subagents take the
mechanical slices: the `0012` migration and column plumbing with real/fake parity, the
`_limit_scope` patterns, the `ReasonerUnavailable` call-site thread-through against the
§4 item 7 mapping table, and the API/type regeneration (`npm run generate:api`).

## 10. Risks

- **Eight items is a large PR.** Items 1-7 are resilience ("how we react to failure");
  item 8 is scheduling ("how we choose"). The commit order lets item 8 be dropped
  without unwinding anything. If review becomes unwieldy, item 8 splits into a
  follow-up.
- **`_limit_scope` is prose-matching and inherently brittle.** Principle 4 contains the
  damage: an unmatched message becomes `UNKNOWN_CAPACITY` and takes the safe generic
  path.
- **The `model_id = '*'` sentinel** for provider-wide circuit rows is a schema
  compromise forced by SQLite's `NOT NULL` primary-key columns. It must not leak past
  the repository boundary; a test asserts callers only ever pass `None`.
- **Indefinite waiting is only as safe as the ceiling.** If an operator sets the ceiling
  absurdly high, a misconfiguration looks like an outage for that long. The
  `provider_waiting` field and event exist so the wait is visible before the ceiling
  expires.
- **Tier substitution can mask a bad binding.** If the preferred model is permanently
  broken, selection silently runs on a lower tier. The attempt records the substitute
  and a substitution emits an event, so the pattern is observable rather than invisible.

## 11. What the build corrected in this design

Recorded because each one was a defect in the spec, not a change of mind. A doc
that contradicts the code is a bug in the doc.

1. **Item 2 as specified would not have fixed the reported symptom.** Removing the
   circuit latch left `should_retry` enforcing the per-task budget, so a task still
   exhausted `kind_max_attempts` and reached `fail_task` a few attempts later,
   opening the same goal block. Inside the ceiling, capacity failures now bypass the
   per-task budget too. `REQUEST_CONCURRENCY` opens no circuit and therefore keeps
   its budget as the bound, so no path is unbounded.

2. **`opened_at` was being restamped on every failure**, which pegged the outage age
   at ~0 and made any duration-based ceiling unreachable. It is now preserved as the
   outage start.

3. **The `provider_capacity` block's circuit evidence ref was hand-built from
   `spec.model_id`**, ignoring the scope-aware key. For an account-level limit the
   circuit is stored provider-wide, so the ref named a nonexistent row and
   `wait_and_retry` cleared nothing, latching the circuit permanently. Both sites now
   share a codec (`circuit_ref` / `parse_circuit_ref`), tested for round-trip. The
   block-kind decision also hardcoded `RATE_LIMIT`, so a connection outage past the
   ceiling advertised `retry_stage` instead of `wait_and_retry`.

4. **The stale-probe bound is not the worker lease.** The spec said `lease_seconds`
   (60s), but an attempt runs up to `agent_runner.timeout_seconds` (600s), so a
   lease-length cutoff would let a second runner steal the probe mid-attempt and
   rebuild the herd. It is `probe_stale_after_seconds` (900s) and must exceed the
   attempt timeout.

5. **Item 7's planning outage start could not be read the way §4 described.**
   Scanning the ledger for `BACKING_OFF` rows finds nothing: `_start_operation` flips
   the reused row back to `STARTED` at the top of every tick. The outage start is the
   active `PlanningOperation`'s `created_at`, which survives reuse. The quarantined
   legacy path records no operation at all and keeps its original attempt budget
   rather than waiting forever.

6. **Item 6 needed a domain un-freeze after all.** `ModelProvider` and `IAModel` are
   domain entities, so "columns on provider/model rows" could not reach the app layer
   without adding fields to them. Un-freeze #16 (decision 56) covers three optional
   fields; the alternative was namespaced config keys, and the typed-column route was
   chosen deliberately for catalog visibility. Migration split into `0012`
   (circuit columns) and `0013` (provider/model capacity).

7. **Item 8 routes on the admission gate without a threshold.** The spec's single
   rule ("substitute when unavailable *and* the wait exceeds the threshold") would
   never substitute for a saturated cap, whose projected wait is zero — the gate
   would have serialized the work it exists to parallelize. Circuit waits use the
   threshold; at-capacity substitutes immediately.

8. **Single-flight is tested at the repository primitive**, not through two
   `handle_goal` calls. A successful probe correctly retires the circuit, so the
   second goal legitimately proceeds and a handler-level test cannot isolate the
   claim. The atomicity lives in the conditional UPDATE, and the test covers it on
   both backends.

Also added along the way: `DummyBehavior.fail_limit_scope` /
`fail_retry_after_seconds`, without which the dry-run dummy could not reach any of
these branches; and `taxonomy.parse_retry_after_seconds`, promoted from a private
helper rather than imported across modules.
