# Phase 10A audit — sweep 4: the console's error and stale-data states, and doc/code drift

**Date:** 2026-08-11
**Scope:** the last two areas on the unswept list — the frontend's error and
stale-data handling (post-Phase-9), and drift between what the repository says
about itself and what is true.
**Result:** the frontend came out **clean on every property checked**; the drift
sweep found 2 real stale references and closed the gap that let them survive.

This closes 10A's named area list. The frontend section is mostly negative
results, which is the honest outcome and is recorded so the next sweep does not
re-derive it.

Suite: 1507 → **1509 passed**, 7 skipped.

---

## The console — verified clean

### Every SSE event the backend emits has a listener

Events are delivered by NAME (`event: <type>`), so a type the client does not
register a listener for is **silently dropped** — the UI would simply go stale
with no error anywhere. Comparing the two lists mechanically:

```
backend domain events : 29
frontend listeners    : 30   (29 + "agent.event")

EMITTED BY BACKEND, NO FRONTEND LISTENER : none
LISTENED FOR BUT NEVER EMITTED           : none
```

Exactly in sync, in both directions.

### Every listened event actually invalidates something

A listener that fires but invalidates no query is the same staleness with an
extra step. The bridge has 10 explicit `case`s and a `default` that invalidates
only for two named sets, so the question is what falls between them:

```
listened for   : 30
explicit cases : 10
TASK_EVENTS    : 8
STATE_EVENTS   : 13

LISTENED FOR, BUT NO CACHE INVALIDATION AT ALL : none
```

### The reconnect gap is resynced

`subscribeToEvents` distinguishes a hard close from an automatic retry, and both
paths end at `onopen` with `hadError` still set, which fires `onReconnect` →
`qc.invalidateQueries()`. Events emitted during the gap are gone, and the client
re-reads everything rather than trusting a cache that missed them.

### Views handle a failed query

`isError` appearing in only one component looked like a gap and is not one: the
views destructure `error` and branch on it before rendering. `Plans`, `Overview`
and `Agents` render a shared `ErrorState` with a retry button and a message
naming the expected API URL; `Goals` and `Activity` render an inline error;
`ReadinessSection` has its own. The one `isError` user, `CycleReviewPanel`,
returns `null` deliberately — it is a supplementary panel, not a primary view.

**Not claimed:** that the console is correct under every failure. This checked
the specific properties the roadmap named — error states and stale data — by
reading and by diffing the event contract mechanically. It is not a behavioural
test of the UI under a flapping backend, and nothing here should be read as one.

---

## Findings — drift

### F11 — two source comments cited paths that do not exist

**Severity:** documentation drift, in the one place nothing was checking.
**Status:** fixed, and the gap closed mechanically.

`tests/unit/test_documented_paths_exist.py` already fails the build when
`CLAUDE.md` or `docs/architecture/*` names a path that does not exist. Its
docstring explains why it exists: the P8.7 audit found CLAUDE.md's own tree
naming `backend/src/domain/`, a directory that never existed, *"in the one file
whose header claims it OVERRIDES default behaviour"*.

A comment in the code makes the same kind of claim, and nothing checked those.
Scanning the shipped source found two live ones:

| file | claimed | reality |
|---|---|---|
| `frontend/src/lib/api.ts:764` | `backend/src/api/security.py` | `backend/agent_orchestrator/api/security.py` |
| `infra/db/migrations/versions/0001_core.py:5` | `docs/DESIGN_NOTES.md` | never committed — no such file in git history |

The first is the *same* never-existed layout that test was written about, still
being repeated in current code two refactors later. The second points at a
document that was never in the repository at all; the pre-refactor design set it
was gesturing at is archived in `docs/history/pre-refactor/`, which the comment
now says.

**Fix.** Both comments corrected, and
`test_no_source_comment_cites_a_path_that_does_not_exist` added beside the
existing document check, reusing its `_ROOTS`/`_SUFFIXES` discipline.

**Scope is deliberately narrow**, following the existing test's own reasoning
that *"a check that cries wolf gets deleted rather than fixed"*:

- **shipped source only** (`backend/agent_orchestrator/`, `frontend/src/`).
  `tests/` is excluded because test bodies carry synthetic paths as fixture data
  (`docs/x.md`), and `test_documented_paths_exist.py` itself must keep naming
  `backend/src/domain/` in order to describe the defect it exists for.
- **the bare `infra/` prefix stays excluded**, exactly as the document check
  excludes it: the docs and comments use it to mean the backend package
  (`backend/agent_orchestrator/infra/`), while the repository also has a
  top-level `infra/` for the dev VM. All ten `infra/`-prefixed comment
  references were resolved by hand against the package and **all ten exist**.

It carries its own extractor self-check, because the comment form is not
backticked and the document extractor would have missed every one of these.
Verified capable of failing: reintroducing the `backend/src/api/security.py`
comment fails it with the exact file and claim named.

## Not findings

Three candidates the scan raised that are correct as written, recorded so the
next person does not "fix" them:

- `app/ports.py:10` — *"task-attempt OS confinement is an infra/execution
  concern"*. English prose, not a path.
- `backend/scripts/dev.sh:30` — `--env-file` defaulting to `backend/.env`, a
  file the operator may create. The help text says environment files are
  optional.
- `docs/superpowers/specs/*` cite `backend/src/...` throughout. Those are dated
  design specs recording a decision at a point in time, like `docs/history/`;
  rewriting their paths would falsify the record rather than fix it. They are
  out of the checker's scope for that reason.

## Phase 10A area list — status

| Area | Sweep | Outcome |
|---|---|---|
| API error paths and edge cases | 1 | 4 findings, fixed |
| Secret handling and the auth surface | 1 | 2 findings, fixed |
| Migration / upgrade paths | 1 | clean (0 schema drift, full cascade) |
| Lease + goal-lease under contention | 2 | clean; 2 suite defects found and fixed |
| Reasoner tool surface vs hostile output | 3 | 2 findings, fixed |
| Frontend error and stale-data states | 4 | clean on every property checked |
| Doc/code drift | 4 | 1 finding, fixed + locked mechanically |

Eleven findings, all proven before being written down and all fixed with a
regression test. Four retractions. Nothing on the original list is unswept.
