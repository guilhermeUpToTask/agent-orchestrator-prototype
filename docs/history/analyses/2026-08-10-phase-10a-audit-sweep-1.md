# Phase 10A audit — sweep 1: auth, secrets, validation, migrations

**Date:** 2026-08-10
**Scope:** the control-plane auth surface, secret handling, API input
validation, and the migration/upgrade path.
**Result:** 6 findings, all proven and all fixed; 3 areas verified clean; 1
hypothesis retracted before it was written down as a finding.

The rule this phase is run under is the only thing that makes the list below
worth reading:

> A finding is only a finding with concrete proof. Never an assumption.

Every entry names how it was proven. The reproductions live in the commit's
tests; the throwaway probe scripts are described well enough to rebuild.

The baseline before any change: **1472 passed, 7 skipped** — so none of these
were caught by the suite, and each one names why the suite could not see it.
After: **1493 passed, 7 skipped**, `ruff` clean, `mypy` clean over 231 files,
frontend 52 passed and building.

---

## Findings

### F1 — the API's own documentation answered anonymously

**Severity:** information disclosure.
**Status:** fixed.

With `ORCHESTRATOR_API_TOKEN` set, `/api/openapi.json`, `/api/docs` and
`/api/redoc` all returned **200** to a caller with no token, serving the whole
57-path control-plane schema — every route, every request and response model.
`security.py` claims "every control-plane request must present it".

**Proof.** A `TestClient` against `create_app()` with the token set:

```
GET /api/openapi.json  without token -> 200   (57 paths, title "Praxis Orchestrator API")
GET /api/docs          without token -> 200
GET /api/redoc         without token -> 200
GET /api/providers     without token -> 401
GET /health            without token -> 200   (deliberately open)
```

**Why the suite could not see it.** `test_control_plane_auth.py` parametrizes
over `create_app().openapi()["paths"]`, which is the right instinct — it covers
routes that do not exist yet. But FastAPI mounts its own doc routes on the bare
app with `include_in_schema=False`, so they are *absent from the inventory the
test trusts*, and no router-level dependency reaches them. The test was blind
exactly where the API describes itself.

**Fix.** `docs_url`/`redoc_url`/`openapi_url` are `None`, and the three routes
are re-registered on the app behind the same `Depends(require_api_token)` as
everything else. They are still `include_in_schema=False`, so they remain
invisible to the parametrized sweep — `test_the_api_documentation_is_guarded`
names them explicitly, because a route that cannot be discovered has to be
listed.

---

### F2 — a 422 echoed the submitted `api_key` back to the caller, and the console rendered it

**Severity:** credential disclosure.
**Status:** fixed.

`POST /api/providers` with a missing field returned FastAPI's default validation
error, whose every entry carries `input` — **the value that failed**. For a
`missing` error that value is the *whole submitted body*, so the plaintext
provider API key travelled straight back:

```json
{"detail":[{"type":"missing","loc":["body","name"],"msg":"Field required",
  "input":{"base_url":"https://api.example.com","api_key":"sk-live-DO-NOT-LEAK-abc123"}}]}
```

This contradicts three separate written commitments: `reference.py`'s "No route
echoes a key", `exceptions.py`'s claim to be "the ONE error -> HTTP mapping
layer", and CLAUDE.md's "Never log secrets".

**It did not stop at the response.** `frontend/src/lib/api.ts::request` throws
`Error("POST /path → 422: <raw body>")`, and `toast.ts::errorDetail` recognised
`{error:{…}}` and a **string** `detail` — but FastAPI's `detail` is an
**array**, so it fell through to `return message` and the raw body went to
`toast.error(...)`. The operator saw their own key in a toast.

**Proof.** The backend half by `TestClient` (output above). The frontend half by
feeding that captured body through `errorDetail`, which returned:

```
POST /api/providers → 422: {"detail":[{"type":"missing","loc":["body","name"],
"msg":"Field required","input":{"base_url":"https://api.example.com",
"api_key":"sk-live-DO-NOT-LEAK-abc123"}}]}
```

**Fix.** Both halves, because either alone leaves the other one bad input away
from repeating it:

- `RequestValidationError` is now registered in `exceptions.py` and answers the
  one envelope as `VALIDATION_ERROR`. It reports **locations and pydantic's rule
  text** (`body.name: Field required`) and never `input` or `ctx`. Pydantic's
  `msg` describes the rule, never the value, so it is safe to pass through — the
  operator can still fix the request.
- `errorDetail` no longer falls back to the raw body. An unrecognised shape now
  yields the method and path only. A helper whose failure mode is "print
  whatever came back" is the amplifier here, not the root cause, and it is worth
  removing on its own terms.

---

### F3 — every validation error was outside the one error envelope

**Severity:** contract inconsistency (and the vehicle for F2).
**Status:** fixed with F2.

`exceptions.py` documents itself as the single error→HTTP map, and the console
parses `{error:{code,message,request_id}}`. The single most common client error
— a schema rejection — answered `{"detail":[…]}` instead, so no client could
read a 422's code and the console degraded to printing raw text. Proven by the
`envelope keys: ['detail']` vs `['error']` contrast in the same probe as F2.

---

### F4 — a caller-supplied `limit` could ask for the entire table

**Severity:** unbounded read / denial-of-service surface.
**Status:** fixed.

`list_planning_artifacts` (`limit: int = 20`) and `agent_events`
(`limit: int = 200`) declared bare ints. The value reaches `LIMIT :limit`
verbatim, and **SQLite reads a negative limit as "no limit"**. `agent_events` is
the fine-grained telemetry table — the largest one a plan produces.

**Proof.** 750 events written for one plan, then:

```
limit=default      -> 200 rows
limit=200          -> 200 rows
limit=-1           -> 750 rows      <-- the whole table
limit=1000000000   -> 750 rows
```

**Notable:** `tail_lines: int = Query(default=200, ge=0, le=2000)` sits in the
*same module*, bounded correctly. The pattern was known and these two missed it
— which is the Phase 4 "control-plane input validation" class, still open.

**Fix.** `Query(default=20, ge=1, le=200)` and `Query(default=200, ge=1, le=1000)`.

---

### F5 — an empty `api_key` was accepted and stored

**Severity:** low; deferred failure.
**Status:** fixed.

`POST /api/providers` with `api_key: ""` returned **201** and wrote an empty
secret. The failure then surfaces at the first real run as a provider
`AUTH_ERROR`, far from the form where the operator could see the cause. `name`
and `base_url` were equally unconstrained.

**Fix.** `Field(min_length=1)` on all three, on create and update. On update
`api_key` stays `None`-able — `None` means "leave the stored secret alone", and
`""` is not a third meaning worth inventing when the honest reading is a
mistake.

---

### F6 — `npm run generate:api` does not run in the documented dev environment

**Severity:** papercut; drift between a documented command and the guest.
**Status:** fixed.

CLAUDE.md documents `npm run generate:api` as the way to regenerate API types,
and separately warns that inside the `praxis-dev` guest **every** python command
needs a `uv run` prefix. The script hardcoded bare `python`:

```
$ npm run generate:api
sh: 1: python: not found
```

Found by running it, not by reading it — the two instructions are individually
correct and only contradict each other at the point of use.

**Fix.** The script invokes `uv run --project ../backend python …`. Verified by
regenerating: the only schema change is this sweep's own bounds
(`minimum`/`maximum` on the two limits, `minLength` on the provider fields),
which is also the evidence that the committed schema was otherwise current.

---

## Verified clean

Recorded because "we checked and it holds" is a result, and because the next
sweep should not re-derive it.

- **The migration chain does not drift from the ORM metadata.** Tests build
  their database with `Base.metadata.create_all`; a real install is built by
  `alembic upgrade head`. Comparing the two schemas column-by-column — names,
  nullability, types, indexes, and foreign keys including `ondelete` — over all
  **22 tables** produced **0 discrepancies**. The suite is green against the
  schema users actually get.
- **Plan-delete cascade coverage is complete.** All **11** plan-scoped tables
  carry an FK to `plans` with `ON DELETE CASCADE`, including `goal_promotions`
  (0017) and `acceptance_runs` (0018), both added *after* the cascade migration
  0015. The invariant CLAUDE.md states has actually been maintained.
- **Every migration has a downgrade.** All 18 define one with a non-empty body.

## Retracted before it became a finding

**"SQLite has foreign keys disabled, so `ON DELETE CASCADE` does nothing."**
`PRAGMA foreign_keys` returned `0` on a connection in the cascade probe, which
would make F-something out of the whole cascade design. It is wrong: the probe
used a bare `create_engine`, not the application's. `infra/db/engine.py` attaches
`PRAGMA foreign_keys=ON` via a `connect` event listener, so it applies to every
pooled connection. The measurement was real and the conclusion did not follow
from it — the same shape as the P8.6 Task 1 retraction, and cheap to catch only
because the rule is to check before writing it down.

## Not yet swept

Named so the next sweep starts here rather than re-choosing:

- **Lease and goal-lease interaction under real contention.** The area most
  likely to hide something and the most expensive to prove; needs concurrent
  workers against real SQLite, not fakes.
- The frontend's error and stale-data states beyond the toast path above.
- The reasoner tool-call surface against hostile model output.
- Remaining doc/code drift outside the files touched here.

## Hypotheses (unproven — do not act on these without evidence)

- **Token comparison is not constant-time.** `security.py` compares with `!=`,
  which short-circuits at the first differing byte. The code path is real, but a
  remote timing attack against a Python/ASGI stack over a network is not
  demonstrated here, and I have no measurement showing the channel is
  exploitable. Recorded as a hypothesis, not a finding. What would settle it:
  a timing distribution over many requests with a controlled prefix. The fix
  (`hmac.compare_digest`) is one line and harmless, but this phase's rule is
  that a plausible reading of the code is not a finding — so it stays here until
  someone measures it or decides the one-line change needs no measurement.
