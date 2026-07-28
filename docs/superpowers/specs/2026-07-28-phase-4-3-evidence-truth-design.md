# Phase 4.3 — evidence truth — design

- **Date**: 2026-07-28
- **Status**: APPROVED, not yet implemented
- **Scope**: `src/api`, `src/app` (one port, one call site, one naming module),
  `src/infra/db`, `src/infra/git` (adopt the naming module), `alembic`
  (migration **0017**), `fixtures/happy-path-v2`, plus regenerated API types.
  **No domain change, and therefore no un-freeze.**
- **Closes**: capability-matrix gap **G9**, the last launch-critical gap assigned
  to Phase 4.
- **Follows**: P4.1 (access and setup truth, `fc54a41`) and P4.2 (operational
  truth, `52d5875`). This is the last of the three.

## 1. Problem

J7 — *"show me what was verified and where the code went"* — needs four facts.
Three of them are already recorded and trustworthy; they are simply unreachable.
The fourth is recorded in a shape that cannot answer the question.

### 1.1 Accepted evidence is buried four levels deep

`VerificationEvidence` (`domain/entities/execution_contracts.py:134`) carries
everything an auditor wants: `run_id`, `task_revision`, `verification_kind`,
`exact_command`, `exit_code`, `candidate_commit_sha`, `test_commit_sha`,
`bounded_output_ref` and `accepted: bool`. It hangs off
`Task.verification_evidence` (`domain/entities/task.py:39`).

`PlanDetailResponse` (`api/routers/plans.py:277`) serializes the domain entities
wholesale, so the only path to it is
`active_cycle.goals[].tasks[].verification_evidence[]` — inside the full plan
document, which also carries the brief, the chat, every goal contract and every
superseded cycle. There is no way to ask for the evidence alone, and no way to
ask a *superseded* cycle for its evidence except by scanning `cycles[]`.

### 1.2 Protected scope is split in half, and nothing joins it

What a task was *allowed* to touch lives on the contract —
`TaskContract.allowed_scope` and `forbidden_scope`
(`execution_contracts.py:41-42`). What it was *forbidden to weaken* lives on the
bundle — `TestBundle.protected_file_hashes` (`:109`), a path→SHA256 map, with
`criterion_to_tests` (`:110`) tying criteria to test node ids.

Both are served, in different objects, at different depths. An operator asking
"what was this task protected from doing?" must fetch the plan document and join
two structures by hand.

### 1.3 Promoted refs are recorded, but unattributed

The goal→cycle merge SHA **is** persisted today.
`ExecutionHandler._promote_goal` (`app/handlers/execution_handler.py:1298`)
merges at `:1306` and, in the finalize transaction, appends it at `:1367`:

```python
cycle.evidence_refs.append(f"git:{commit_sha}")
```

`Cycle.evidence_refs` (`domain/entities/planning_artifacts.py:178`) is a
`list[str]`. It has no goal attribution, no branch name and no timestamp — a
list of `git:<sha>` in promotion order. It cannot answer "which goal landed
where", which is the half of J7 about *where the code went*.

So the refs themselves are not served at all, and the fixtures reconstruct them
by convention (`fixtures/happy-path-v2/scripts/verify_run.py:390`):

```python
cycle_branch = f"cycle/{cycle_id}"
```

### 1.4 The documented convention is wrong, so reconstruction is unsafe

Reconstructing a ref from the documented ladder produces refs that do not exist.
`infra/git/workspace.py:204-214` takes two different paths:

| | Documented (`CLAUDE.md:79`) | Cyclic path (`workspace.py:205-207`) | Legacy path (`:213-214`) |
|---|---|---|---|
| Ladder | default → `plan/<plan_id>` → `cycle/<cycle_id>` → `goal/<goal_id>` → `task/<task_id>/a<attempt>` | default → `cycle/<cycle_id>` → `goal/<goal_id>` → `task/<task_id>/<run_id>` | default → `plan/<plan_id>` → `task/<task_id>/a<attempt>` |
| `plan/<plan_id>` | a rung | **never created** — the cycle branch is cut straight from the default branch (`:209`) | created (`:216`) |
| Task suffix | `a<attempt>` | `<run_id>` | `a<attempt>` |

The drift has a cause: the local variable at `:206` is named `plan_branch` but
holds the goal branch. `docs/architecture/execution-model.md:168` repeats the
legacy shape as though it were the only one.

This is the argument against serving refs by convention. A read model that
derives `plan/<plan_id>` for a cyclic plan would advertise a branch that was
never created — the same class of defect P4.2 found, where the API advertised an
action the server refused.

### 1.5 Disposition is reachable only through the plan document

`Cycle.output_disposition` and `output_reference`
(`planning_artifacts.py:179-180`), recorded by
`Plan.record_output_disposition` (`aggregates/planner_orchestrator.py:1171`) via
`POST /api/plans/{plan_id}/publication` (`api/routers/plans.py:1194`). Same
problem as §1.1: correct, and reachable only wholesale.

## 2. Non-goals

- **Rendering any of it.** Phase 5 owns the UI.
- **Replacing `backend/scripts/export_plan_runs.py`.** It exports the whole
  database across all plans — catalog, performance comparisons, telemetry, chat,
  circuits. That is an analytics export, not a per-cycle evidence read, and a
  cycle endpoint does not supersede it. The matrix row is re-scoped, not closed
  (§5).
- **Recording task→goal promotions.** `Workspace.commit` returns `None`
  (`domain/ports/workplace_port.py:41`); changing it would be a domain port
  signature change. Task branches are also deleted by design on discard, so a
  task-level promoted ref is not a durable fact. The durable rungs are
  goal→cycle and cycle→disposition, and both are covered.
- **Backfilling pre-0017 cycles.** Mapping existing `git:<sha>` entries to goals
  by promotion order would be a guess. Old cycles serve what they have (§3.3).
- **Changing any existing response field.** Everything here is additive.

## 3. Design

### 3.1 A `goal_promotions` table (migration 0017)

`0016_worker_registry` is the current head.

```sql
CREATE TABLE goal_promotions (
    id          TEXT PRIMARY KEY,
    plan_id     TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    cycle_id    TEXT NOT NULL,
    goal_id     TEXT NOT NULL,
    from_ref    TEXT NOT NULL,
    into_ref    TEXT NOT NULL,
    merge_sha   TEXT NOT NULL,
    promoted_at TEXT NOT NULL
);
CREATE INDEX ix_goal_promotions_cycle ON goal_promotions (plan_id, cycle_id);
```

Unlike P4.2's `workers`, this table **is** plan-scoped, so `ON DELETE CASCADE` is
mandatory under architectural invariant #2 and
`test_delete_plan_leaves_nothing.py` fails without it.

It structures a fact already being written (§1.3) rather than collecting a new
one. `from_ref`/`into_ref` are stored as the adapter actually built them, never
re-derived — that is the whole point of §1.4.

### 3.2 Written through the UnitOfWork, not a side connection

`UnitOfWork` (`app/ports.py:349`) already owns four repositories — `plans`,
`outbox`, `executions`, `goal_leases`. `promotions` becomes the fifth:

- `app/ports.py` — the `GoalPromotionRepository` port plus the UoW property
- `infra/db/` — the SQLite adapter
- `app/testing/fakes.py` — the in-memory twin, because
  `tests/unit/orchestration/` runs the suite through both backends and their
  semantics must stay identical

**This is deliberately not the P4.2 pattern.** `workers` is best-effort
telemetry written on its own connection with failures swallowed, and that was
right for a heartbeat. Evidence is not telemetry. A promotion record that can
silently go missing makes the read model under-report where code went, which
defeats the sub-project. It belongs in the transaction that records the goal
completing.

**It is also the option that reduces lock contention, not the one that adds it.**
`engine.py:43-46` runs `journal_mode=WAL` — concurrent readers and exactly **one
writer** — with `busy_timeout` so contention waits, and `_session.py:34-51`
retries lock errors on exponential backoff before failing with `DB_LOCKED`. The
`db.locked_retry` warning at `:48` exists because that contention is real.

Inside the UoW, the `INSERT` rides in a transaction that already holds the single
writer slot: **no new lock acquisition and no new contender**. On its own
connection it would open a second transaction competing with the plan finalize
path — the hottest write in the system — burning `busy_timeout` and retries, and
reproducing the shape behind the worker self-deadlock P4.2 designed the heartbeat
around (a blocking SQLite wait on the event-loop thread while the lock's owner is
a coroutine waiting for that same loop).

The honest cost is that the finalize transaction holds the writer lock marginally
longer for one extra `INSERT`, once per goal promotion — against an entire extra
transaction and a possible retry storm.

### 3.3 The write seam already exists and is already guarded

`_promote_goal` does the merge *outside* the transaction at `:1306` — correct
under invariant #5 — then opens `with uow:` at `:1340` and re-guards the
promotion reservation at `:1342`, returning `PAUSED` if it was lost.

The row is written next to `:1367`, where `commit_sha` is already in scope and
every guard has passed. So:

- a promotion that lost its reservation records nothing — no phantom row;
- a rollback anywhere in the finalize transaction takes the row with it;
- the row and `plan.complete_goal(goal_id)` (`:1368`) commit together.

No new call site, no port signature change, no domain change.

### 3.3a One home for the branch-naming convention

`from_ref` and `into_ref` must be the branches the adapter *actually built*, but
`merge_goal` returns only a SHA (`workplace_port.py:40`) and widening it is a
domain port change (§2). The handler therefore has to know the names — and
naively that means a third copy of the f-strings, because there are already two:
`workspace.py:205-206` in `begin` and `workspace.py:282-283` in
`_merge_goal_sync` build `cycle/<id>` and `goal/<id>` independently.

That duplication is the structural cause of §1.4's drift, so this design removes
it rather than adding to it. A pure naming module, `src/app/branch_names.py`:

```python
def cycle_branch(cycle_id: str) -> str: ...
def goal_branch(goal_id: str) -> str: ...
def task_branch(task_id: str, run_id: str) -> str: ...
def legacy_plan_branch(plan_id: str) -> str: ...
def legacy_task_branch(task_id: str, attempt: int) -> str: ...
```

`src/app/` is the correct home, and the only legal one. The dependency rule is
`domain -> app -> infra & api`: infra **may** import app, app may **not** import
infra, and the domain is frozen. So app is the one layer both
`infra/git/workspace.py` and `app/handlers/execution_handler.py` can share. Both
call sites in `workspace.py` and the promotion recorder then read from a single
definition, and the convention has exactly one home.

The objection — *branch naming is an infrastructure detail, and putting it in
`app` leaks it upward* — was considered and does not hold. These names are part
of the orchestration contract rather than an implementation choice: CLAUDE.md
documents the ladder as an architectural invariant, the fixtures assert the
branches by name, and the execution handler needs them to record truthful
evidence. `src/app/` already hosts exactly this kind of cross-cutting convention
(`block_policy.py`, `agent_feedback.py`, `promotion_failures.py`).

The module carries **both** shapes — cyclic and legacy — so the §5 documentation
fix has a single authority to cite instead of restating the convention in prose.

This is a small refactor of existing code in the path of the work, not unrelated
cleanup: without it, recording promoted refs would create the third copy of the
convention whose second copy this sub-project exists to correct.

`cycle.evidence_refs.append(...)` at `:1367` **stays**. Removing it would change
a persisted domain field's meaning for existing rows, and `:1194` reads that list
into a block's evidence refs. The new table is additive alongside it.

### 3.4 `GET /api/plans/{plan_id}/cycles/{cycle_id}/evidence`

One read model **per cycle**, exactly as the matrix's test demands — not per
plan, so a superseded cycle's evidence survives a replan and stays addressable.

A new router, `api/routers/evidence.py`, following P4.1's `readiness.py` and
P4.2's `workers.py`. `plans.py` is past 1200 lines; this does not go in it.

```json
{
  "plan_id": "p1",
  "cycle_id": "c1",
  "cycle_status": "completed",
  "goals": [
    {
      "goal_id": "g1",
      "status": "done",
      "promotion": {
        "from_ref": "goal/g1",
        "into_ref": "cycle/c1",
        "merge_sha": "a1b2c3d…",
        "promoted_at": "2026-07-28T12:00:00Z"
      },
      "tasks": [
        {
          "task_id": "t1",
          "revision": 2,
          "status": "done",
          "protected_scope": {
            "allowed_scope": ["src/foo/**"],
            "forbidden_scope": ["tests/protected/**"],
            "protected_file_hashes": {"tests/test_a.py": "…"},
            "criterion_to_tests": {"c1": ["tests/test_a.py::test_x"]}
          },
          "test_bundle": {
            "test_commit_sha": "…",
            "state": "frozen",
            "verification_strategy": "tdd"
          },
          "accepted_evidence": [
            {
              "id": "e1",
              "run_id": "r1",
              "task_revision": 2,
              "verification_kind": "…",
              "exact_command": "pytest tests/test_a.py",
              "exit_code": 0,
              "candidate_commit_sha": "…",
              "test_commit_sha": "…",
              "bounded_output_ref": "…",
              "finished_at": "2026-07-28T11:59:00Z"
            }
          ],
          "rejected_evidence_count": 3,
          "superseded_evidence_count": 1
        }
      ]
    }
  ],
  "disposition": {"disposition": "merge", "output_reference": "…"},
  "unattributed_evidence_refs": []
}
```

Four decisions inside that shape:

**`accepted_evidence` filters on `accepted is True` *and*
`task_revision == task.revision`.** `edit_task` invalidates revision-bound
evidence, so evidence at a stale revision is exactly what this endpoint must not
present as accepted. Evidence excluded by the revision test is reported as
`superseded_evidence_count` rather than dropped silently — an operator who edited
a task should see that prior evidence exists and no longer counts.

**Rejected runs are counted, not dumped.** The full attempt history already has a
home at `GET …/attempts` and `GET …/attempts/{id}/log`. Inlining every rejected
run would reproduce §1.1's problem in a new endpoint.

**Protected scope is joined.** Both halves of §1.2 appear in one
`protected_scope` object per task, which is the join an operator was doing by
hand.

**`unattributed_evidence_refs` carries `Cycle.evidence_refs` for pre-0017
cycles.** A cycle promoted before this migration has SHAs but no rows (§2, no
backfill). Serving them under an honestly-named field beats an empty `promotion`
that implies nothing was promoted. It is empty for cycles promoted after 0017.

`404` for an unknown `cycle_id` **and** for a `cycle_id` that belongs to a
different plan — the cycle is addressed under its plan, so cross-plan reads are
not merely empty, they are refused.

### 3.5 Auth

The route carries `require_api_token` and joins P4.1's parametrized guard test.
Evidence includes commands, commit SHAs and output refs; it is control-plane
data, not public.

### 3.6 The fixture consumes it

`fixtures/happy-path-v2/scripts/verify_run.py` stops reconstructing
`cycle/<cycle_id>` (`:390`) and `goal/<goal_id>` (`:404`) and reads the served
refs instead, still verifying them with `git rev-parse --verify`. That is the
proof the endpoint answers the question the fixture actually asks, and it removes
the by-convention reconstruction §1.4 shows to be fragile.

`happy-path-v1` is locked (CLAUDE.md) and is left reconstructing.

## 4. Data and migrations

Migration `0017_goal_promotions`: one `CREATE TABLE` with an
`ON DELETE CASCADE` FK to `plans`, one index, no backfill. Down-migration drops
both. Single linear head maintained.

## 5. Docs

Per docs discipline, in the same PR:

- **`CLAUDE.md:79`** — correct the ladder to state both paths: cyclic is
  default → `cycle/<cycle_id>` → `goal/<goal_id>` → `task/<task_id>/<run_id>`
  with no `plan/<plan_id>` rung; `plan/<plan_id>` and `a<attempt>` are the legacy
  path.
- **`docs/architecture/execution-model.md:168`** — same correction.
- **`docs/architecture/data-model.md`** — add `goal_promotions`.
- **`docs/architecture/capability-matrix.md`** — the four G9 rows move from
  `api-only`/`hidden` to served; the G9 section closes. The
  "Export a run's evidence bundle" row is **re-scoped, not closed**: it is an
  analytics export and was never the J7 answer (§2).
- **No `docs/decisions/decision-log.md` entry.** Nothing here touches the domain.
  Stated explicitly so the next audit does not go looking for an un-freeze that
  deliberately does not exist.

## 6. Testing

| Area | Test |
|---|---|
| The matrix's literal test | One evidence read model per cycle — accepted evidence refs, protected paths, promoted refs, disposition — asserted against a completed dry-run cycle |
| Promotion atomicity | Truth test in `tests/unit/orchestration/` across fakes **and** real SQLite: a rollback in the finalize transaction leaves no row; a promotion whose reservation was lost records nothing |
| Cascade | `test_delete_plan_leaves_nothing.py` covers the new table by design |
| Replan survival | After `start_replan` and re-activation, the source cycle's evidence still serves |
| Revision binding | `edit_task` bumps the revision → prior accepted evidence leaves `accepted_evidence` and appears in `superseded_evidence_count` |
| Protected scope join | Both contract scope and `protected_file_hashes` appear under one task |
| Disposition | Served after publication; `discard` serves the disposition with a null `output_reference` |
| Pre-0017 cycles | A cycle with `evidence_refs` but no rows serves `unattributed_evidence_refs` and null `promotion` |
| Isolation | 404 for unknown `cycle_id` and for a `cycle_id` owned by another plan |
| Auth | Route rejects without a token, via P4.1's parametrized guard test |
| Migration | `test_migrations.py` upgrade/downgrade round-trip |
| Branch naming | Recorded `from_ref`/`into_ref` resolve to real branches — `git rev-parse --verify` against a real repo in the integration walk, so the naming module and the adapter cannot drift apart silently |
| Fixture | `happy-path-v2` verifies promoted refs through the endpoint |

## 7. Risks and rejected alternatives

**Rejected: reconstructing refs by convention.** Zero cost, and §1.4 shows the
documented convention is already wrong — it would advertise `plan/<plan_id>` for
cyclic plans, a branch that is never created. It is also wrong after a discard.
This is the same defect class P4.2 found: the API asserting something the system
does not do.

**Rejected: resolving refs live against git at read time.** Truthful with no
storage, but it puts filesystem and `git` I/O inside a `GET`, gives the read
unbounded latency, and returns nothing once the repository is moved or
unreachable from the API host.

**Rejected: recording task→goal promotions.** Requires changing
`Workspace.commit` from `-> None` to `-> str` in `domain/ports/workplace_port.py`
— a domain port signature change needing un-freeze #20 — to record refs that are
deleted by design when an attempt is discarded.

**Rejected: populating the dead `CycleEvidence` type.**
`execution_contracts.py:156` declares `CycleEvidence` (`cycle_id`, `commit_sha`,
`verification_evidence_refs`, `commands`, `accepted_at`) and **nothing in `src/`
or `tests/` references it** — someone designed this read once and never wired it.
Populating it would need a new field on `Cycle` to hold it, which is a domain
change for a shape the API layer can project without one. Left dead; recorded
here so the next reader knows it was considered.

**Rejected: widening `merge_goal` to return the branch names alongside the SHA.**
The most direct way to give the handler truthful refs, and it changes a signature
in `src/domain/ports/` — the same domain port change rejected two paragraphs
above for task→goal promotions. §3.3a gets the same truthfulness from a shared
naming module without touching the frozen layer.

**Rejected: writing promotions best-effort on their own connection.** It is the
P4.2 `workers` pattern and needs no UoW change, but a lost evidence record is a
read model that under-reports. Telemetry may be best-effort; evidence may not.

**Risk: the read model grows unbounded on a large cycle.** Mitigated by counting
rejected and superseded evidence instead of inlining it (§3.4), which bounds the
response to one entry per accepted task revision.

## 8. Exit criteria

Closes, for Phase 4:

- ✅ G9 — one evidence read model per cycle returning accepted evidence refs,
  protected paths, promoted refs and the disposition, asserted against a
  completed dry-run cycle.
- ✅ *Tier 0/Tier 1 need no direct SQLite edit or hidden env fallback* — the
  fixture reads promoted refs over HTTP instead of reconstructing them.
- ✅ *Integration tests cover each new/corrected contract.*
- ✅ *OpenAPI describes the cyclic lifecycle; generated types current.*

With P4.1 and P4.2 merged, this closes **Phase 4**.
