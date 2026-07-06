# Domain design decisions (formerly `backend/docs/DESIGN_NOTES.md`)

> Code comments and tests referencing "DESIGN_NOTES #N" point at the numbered
> entries in this file.

> **RESOLVED (2026-07-02, Phase-0 domain freeze):** every entry below was decided by
> adopting its **recommended option** — #1 AWAITING_REVIEW/REVIEW are the gates
> (`pause_after` removed); #2 typed `FailureKind` on `TaskResult`, wired to
> `RetryPolicy.non_retryable_kinds` with the shared taxonomy (token_limit/auth_error
> terminal); #3 `Task.reopen()`/`Goal.reopen()` via `Plan.reopen_task()`, tracked on
> `reopen_count`; #4 single result slot (history in events); #5 capability-id contract
> enforced at the edit boundary (`UnknownCapabilityError`); #6 `Status` moved to
> `value_objects/lifecycle.py`; #7 no generic Entity base; #8 edits reuse
> `lookups.find_task` (`TaskNotFoundError`); #9 `TaskResult.success()` tightened;
> #10 errors stay grouped by topic (README wording fixed). The entries are kept below
> as the decision record.

These are the domain-layer questions that are **real design choices**, not settled facts
(settled explanations live in each package's `README.md`). Each entry states the concern,
where it lives, the current state, the options with trade-offs, and a recommendation. None
of these block the current code; they're calls to make deliberately before building the app
and infra layers on top.

---

## 1. Human-review gate: `pause_after` vs `AWAITING_REVIEW`
**Where:** `domain/aggregates/planner_orchestrator.py` (`PlanPhase`, `pause_after`,
`should_pause`).

**Current state:** two half-implementations of the same idea coexist — a `pause_after` set
of phases *and* a `PlanPhase.AWAITING_REVIEW` value. `should_pause()` reads `pause_after`;
nothing yet uses `AWAITING_REVIEW`.

**Options**
- **A — Make `AWAITING_REVIEW` the single gate (recommended).** On finishing a gated phase,
  the worker transitions the plan *into* `AWAITING_REVIEW` and blocks there until an explicit
  `approve`/`resume` command advances it. `pause_after` becomes just the declaration of
  *which* phases are gated. One concept, one place a human unblocks, visible in the phase.
- **B — Keep `pause_after` only, drop `AWAITING_REVIEW`.** Pause is implicit (worker stops,
  lease released); resume = re-claim. Simpler, but the "waiting for a human" state isn't
  visible in the plan's phase, which the UI probably wants.

**Recommendation:** A. It matches "every human-review need uses the same gate" and makes the
waiting state a first-class, queryable phase.

---

## 2. Retry classification: magic strings → typed `FailureKind`
**Where:** `domain/policies/retry_policies.py` (`non_retryable_reasons`), consumed via
`TaskResult.failure_reason`.

**Current state:** `non_retryable_reasons: list[str] = ["invalid_input"]`, exact-matched
against a free-form reason string.

**Problem:** brittle (`"invalid input"`, `"InvalidInput"`, a typo all slip through and get
retried) and under-specified (auth/permission failure, missing/deleted agent,
capability-no-longer-satisfied, unrecoverable config are almost certainly permanent too).

**Options**
- **A — `FailureKind` enum (recommended).** Add a typed `kind: FailureKind` to `TaskResult`
  (`TRANSIENT`, `INVALID_INPUT`, `AUTH`, `AGENT_MISSING`, `CAPABILITY_UNSATISFIED`,
  `UNRECOVERABLE`, …); `should_retry` matches on the enum. "Retryable?" becomes a checked
  classification, not string bingo.
- **B — Keep strings but normalize + expand the list.** Cheaper, still stringly-typed and
  easy to drift.

**Recommendation:** A, when the agent adapter that produces failures lands (it's the producer
that must set the kind).

---

## 3. Granular redo / `reopen` (a human dislikes a good result)
**Where:** `domain/entities/task.py`, `goal.py`, and `Plan` in the aggregate.

**Current state:** `requeue()` only allows `RUNNING`/`FAILED → PENDING`. A `DONE` task is
`TERMINAL` and cannot be re-run, so there is no path for "the result succeeded but the human
wants it redone."

**Options**
- **A — Add `Task.reopen()` (`DONE → PENDING`) driven by the review gate (recommended).**
  Clears `result`, does **not** count as a failure attempt (or counts on a separate counter),
  exposed via `Plan.reopen_task()` so the invariant (only reopen a `DONE` task) stays at the
  root. Optionally mirror with `Goal.reopen()` to re-open a finished goal. Cost: `DONE` is no
  longer strictly terminal on that path — the scan already re-selects any non-terminal task,
  so this "just works," but it's a deliberate semantic change to acknowledge.
- **B — No reopen; redo = create a new task.** Keeps terminal states pure; loses the "same
  task, new attempt" identity and its history.

**Recommendation:** A, gated behind human review, with reopen attempts tracked separately from
failure attempts so backoff/retry math stays meaningful.

---

## 4. Task result history vs single slot
**Where:** `domain/entities/task.py` (`result`), `value_objects/tasks_vos.py`.

**Current state:** `result` is a single slot, overwritten on `requeue()`; prior attempts are
not kept on the aggregate.

**Options**
- **A — Keep single slot; history lives in telemetry/events (recommended).** `TaskFailedEvent`
  / `TaskRequeued` carry the reason and `agent_events` stream the full per-attempt run. The
  aggregate stays lean and can't desync.
- **B — Add `result_history: list[TaskResult]`.** Only worth it if a *use case* needs prior
  attempts to make a decision (e.g. feed the last failure into the next attempt's prompt, or
  show the user a diff across retries). Otherwise it's duplicated state.

**Recommendation:** A, unless/until a concrete use case needs prior results *for logic* (not
just display) — then B, scoped to what that use case reads.

---

## 5. `required_capabilities`: ids vs names (correctness contract)
**Where:** `domain/entities/task.py` (`required_capabilities: list[str]`) and
`domain/services/capability_matching.py`.

**Current state:** `match_agent()` compares the task's strings against
`{c.id for c in agent.capabilities}` — so the strings **must be capability ids**. If anyone
populates them with human-readable names/tags, matching silently fails and every task falls
back to the default agent.

**Options**
- **A — Pin the contract to ids and document it (recommended).** Keep `list[str]` = capability
  ids; validate at the edit/create boundary that each id exists in the catalog (raise
  `UnknownCapabilityError` otherwise) so a bad id fails loudly instead of silently defaulting.
- **B — Match on names.** Only if names are guaranteed unique and stable; ids are the safer key.

**Recommendation:** A. Keep it `list[str]` (not embedded entities — that would bloat the
aggregate and go stale), but add the existence check so the id contract is enforced, not assumed.

---

## 6. `Status` VO placement
**Where:** `domain/value_objects/tasks_vos.py`.

**Current state:** `Status` (+ `TERMINAL`) is shared by `Goal` and `Task` but lives in a file
named for tasks.

**Options:** **A —** move to `value_objects/lifecycle.py` (or `status.py`) and re-import.
**B —** leave it.

**Recommendation:** A eventually; it's a rename-only cleanup touching ~5 importers (goal, task,
navigation, edit_service, aggregate) with zero behavior change. Low priority.

---

## 7. Generic `Entity` base
**Where:** `domain/entities/base.py` (currently a placeholder).

**Current state:** entities use plain `str` ids on Pydantic `BaseModel` and share no behavior.

**Options**
- **A — Introduce a base only when duplication appears (recommended).** A shared base could
  give a typed id and identity `__eq__`/`__hash__` (equality by id, not by value) and a common
  version/`_bump` hook. Today it mostly adds Pydantic-generics friction for little gain.
- **B — Add it now** for uniformity.

Sketch for when A's trigger hits:
```python
IdT = TypeVar("IdT")
class Entity(BaseModel, Generic[IdT]):
    id: IdT
    def __eq__(self, other: object) -> bool:
        return isinstance(other, type(self)) and other.id == self.id
    def __hash__(self) -> int:
        return hash((type(self).__name__, self.id))
```
**Recommendation:** A. Introduce it the first time you need identity equality or a shared
`_bump`, not before.

---

## 8. Edit lookup error convention
**Where:** `domain/services/edit_service.py` (`edit_task_requirements`) vs
`domain/services/lookups.py` (`find_task`).

**Current state:** `edit_task_requirements` re-implements a task lookup with `next(...)` and
raises `InvalidEditError`, while `lookups.find_task` does the same traversal but raises
`TaskNotFoundError`. Two ways to express "task not found."

**Options**
- **A — Reuse `find_task` everywhere; let edits surface `TaskNotFoundError` (recommended).**
  DRY; one lookup, one error. The API error mapper can present it however it likes.
- **B — Keep edit-specific `InvalidEditError`** but centralize the lookup in one private helper
  in `edit_service` so it isn't re-written per function.

**Recommendation:** A, unless the edit flow genuinely needs a distinct error code for the API —
then B. Either way, stop hand-rolling the traversal in each edit function.

---

## 9. `TaskResult.success(**kw)` looseness
**Where:** `domain/value_objects/tasks_vos.py`.

**Current state:** `success(cls, output, **kw)` is typed `object` with a `# type: ignore`, so
it accepts arbitrary kwargs Pydantic then validates.

**Recommendation:** tighten to explicit optional params (`artifacts`, `metadata`) and drop the
`type: ignore`. Small, safe, removes a footgun. (The sibling `failure()` had a real bug —
`output="output"` hardcoded — already fixed.)

---

## 10. One error type per file
**Where:** `domain/errors/tasks_errors.py` (in-file comment) vs `domain/errors/README.md`.

**Current state:** `tasks_errors.py` groups goal *and* task errors, while the errors README says
"one exception per failure case." The grouping is fine functionally; it just contradicts the
stated convention.

**Options:** **A —** keep grouped-by-topic files and soften the README wording to
"one exception per failure *case*, grouped by topic." **B —** split to one-type-per-file.

**Recommendation:** A. Grouping cohesive errors per topic is more practical than a file per
class; make the README match reality.
