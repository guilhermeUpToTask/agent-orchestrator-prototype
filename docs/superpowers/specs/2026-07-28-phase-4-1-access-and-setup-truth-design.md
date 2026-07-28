# Phase 4.1 — access and setup truth — design

- **Date**: 2026-07-28
- **Status**: APPROVED, not yet implemented
- **Scope**: `src/api`, `src/infra/git`, `src/infra/cli`, `frontend/src/lib`, plus
  regenerated API types. **No domain change, no migration.**
- **Closes**: capability-matrix gaps **G1**, **G6**, **G11**, and the readiness
  half of ROADMAP Phase 4's investigate list.
- **Related**: [`docs/architecture/capability-matrix.md`](../../architecture/capability-matrix.md)
  is the audit that produced these gaps; P4.2 (operational truth) and P4.3
  (evidence truth) follow this sub-project.

## 1. Problem

Three defects, all found by the Phase 3 audit, all in the path an operator walks
**before their first plan finishes**.

### 1.1 The token guard covers the catalogs, not the product

`require_api_token` (`backend/src/api/security.py:22`) states its own contract:

> when set, every control-plane request must present it via
> `Authorization: Bearer <token>` or `X-API-Token`.

It is declared on five routers — `reference`, `config`, `reasoner`, `runner`,
`metrics`. It is **not** declared on `plans.router`
(`backend/src/api/routers/plans.py:83`) or `events.router`
(`backend/src/api/routers/events.py:21`):

```python
router = APIRouter(prefix="/plans", tags=["plans"])   # no dependencies=
router = APIRouter(tags=["events"])                   # no dependencies=
```

With `ORCHESTRATOR_API_TOKEN` set, **36 of the 64 served operations** answer an
unauthenticated caller: every gate approval, `POST …/publication`,
`DELETE /api/plans/{plan_id}`, `POST …/edits`, and `GET /api/plans/{plan_id}`
with the plan's brief and chat history. `test_control_plane_token_guard`
(`tests/integration/test_api.py:977`) exercises exactly one operation,
`GET /api/providers`, so the suite reports the guard as covered.

Bounded in practice by `api start --host 127.0.0.1` and by the fixtures sending
the token on every call — which is precisely why nobody noticed: the operator
material behaves as if the guard were universal.

**The root cause is not two missing declarations. It is that the guard is
opt-in**, so any router — including ones not yet written — is unguarded by
default. A fix that adds two `dependencies=` arguments leaves the next router
free to repeat the defect.

### 1.2 A mistyped `repo_url` produces a green run against an empty repository

`POST`/`PUT /api/projects` (`backend/src/api/routers/reference.py:294`, `:303`)
store `repo_url` with no validation of any kind.

The failure is not a late, loud error. `ProjectWorkspaceResolver._default_branch`
(`backend/src/infra/git/project_workspace.py:66-67`) returns `"main"` for a path
with no `.git`:

```python
if not (repo / ".git").exists():
    return "main"
```

and `GitBranchWorkspace` then creates it (`backend/src/infra/git/workspace.py:150-153`):

```python
self._repo.mkdir(parents=True, exist_ok=True)
subprocess.run(["git", "init", str(self._repo)], check=True, capture_output=True)
...
_git(self._repo, "commit", "--allow-empty", "-m", "chore: initial commit")
```

So a typo, a renamed directory, or a path that was correct yesterday yields a
brand-new empty repository, and the plan runs to a **green publication against
nothing**. `tests/unit/test_fixture_docs_contract.py` already records the
adjacent version of this (a project with no `repo_url` silently gets a scratch
repo) as a documentation defect; the typo path has the same consequence and no
guard at all.

### 1.3 Readiness is assembled by hand

`GET /api/reasoner/status` and `GET /api/runner/status` each answer part of "can
this machine run a plan?". Nothing answers whether the master key is present,
whether the catalogs contain a usable agent/provider/model triple, or whether a
project's repository resolves — so the J1/J2 setup jobs are a sequence of calls
plus operator inference, and the first symptom of an incomplete setup is a
failed run.

## 2. Non-goals

- **Real authentication.** The shared prototype token stays exactly as scoped in
  `security.py`. This sub-project changes *where it is enforced*, not what it is.
- **Rendering any of this.** Phase 5 owns the UI. The only frontend change here
  is the two lines that keep the live feed working once the guard is on.
- **Network validation of remote repositories.** No clone, no reachability probe
  at write time; see §3.4.
- **Worker health, capacity reads, evidence reads.** P4.2 and P4.3.

## 3. Design

### 3.1 The guard becomes default-on

Delete `dependencies=[Depends(require_api_token)]` from all five routers that
declare it, and apply it once where routers are mounted
(`backend/src/api/server.py:127-134`):

```python
_prefix = "/api"
_guarded = [Depends(require_api_token)]
app.include_router(plans.router,     prefix=_prefix, dependencies=_guarded)
app.include_router(reference.router, prefix=_prefix, dependencies=_guarded)
...
app.include_router(events.router,    prefix=_prefix, dependencies=[Depends(require_api_token_or_query)])
```

`/health` is registered directly on the app (`server.py:136`) and is deliberately
**not** guarded: a liveness check that requires a secret cannot serve the setup
checklist it exists for.

Enforcement is inventory-driven, the same shape as
`tests/unit/test_capability_matrix.py`: parametrize over
`create_app().openapi()["paths"]` and assert every operation except `/health`
answers 401 without a token. A router added later is covered before it is
written, which is the property §1.1 says is missing.

### 3.2 `/api/events` accepts the token in the query string

`subscribeToEvents` uses `new EventSource(...)` (`frontend/src/lib/api.ts:563`),
and EventSource cannot set request headers. A second dependency, used **only by
the events router**, accepts the same token from `?token=`:

```python
def require_api_token_or_query(
    authorization: str | None = Header(default=None),
    x_api_token: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> None:
```

Confining the URL-borne token to the one route that cannot use a header keeps
the tradeoff visible, and makes it testable in both directions: `/api/events`
accepts `?token=`, and no other operation does.

### 3.3 uvicorn's access log is turned off

A token in a query string is only dangerous where it is written down.
`RequestLoggingMiddleware` already logs every request structurally and records
`request.url.path` (`backend/src/api/middleware/request_logging.py:59`) — the
path, never the query string. uvicorn's own access logger would print
`GET /api/events?token=… HTTP/1.1`, so `api_start` passes `access_log=False`:

```python
uvicorn.run(create_app(), host=host, port=port, access_log=False)
```

This also retires a stdlib-logging path that the project's observability rules
already forbid, and loses nothing: the structured middleware logs the same
request with a correlation id, status and duration.

### 3.4 Write-time binding validation

A new `backend/src/infra/git/repository_binding.py` exposes:

```python
def validate_repo_url(repo_url: str | None) -> RepositoryBinding
```

returning a frozen `RepositoryBinding(kind, resolved_path, default_branch)` where
`kind` is `local | remote | scratch`, and raising `ProjectBindingInvalidError`
(code `PROJECT_BINDING_INVALID`) otherwise. Rules:

| `repo_url` | Verdict |
|---|---|
| absent / empty | `scratch` — legal, and named as such so the auto-seeded empty repo is a choice rather than a surprise |
| local path or `file://` | must exist, must contain `.git`, must have a determinable default branch — otherwise 422 naming which of the three failed |
| any other scheme | `remote` — syntax only. **No clone, no network call at write time**: a create request must not block on a slow or unreachable host, and a repository that is reachable now may not be at execution time, so the check would buy little and cost a timeout |

The router calls it exactly as `reasoner.py` calls `validate_reasoner_config` and
`runner.py` calls `validate_agent_binding` — infra function, thin router, one
error map. `PROJECT_BINDING_INVALID → 422` joins `_STATUS_BY_CODE`
(`backend/src/api/exceptions.py`) beside `REASONER_CONFIG_INVALID`.

Existing rows are untouched: validation runs on write, not on read, so a
pre-existing bad binding surfaces through §3.5 and §3.6 rather than by making
`GET /api/projects` fail.

### 3.5 A named repository is never initialized

`GitBranchWorkspace.__init__` gains `allow_init: bool` (default `True`, so no
existing caller changes meaning), and `ProjectWorkspaceResolver.resolve` sets it
from the binding:

```python
allow_init = project.repo_url is None      # only a scratch project may be created
```

With `allow_init=False`, the missing-repository branch at `workspace.py:150`
raises instead of running `git init`. It raises `TaskFailed`, which this file
already uses for git failures (`workspace.py:216`, `:272`, both
`FailureKind.TOOL_ERROR`) — but with `FailureKind.AUTH_ERROR`, the **terminal,
non-retryable** kind, following the treatment CLAUDE.md specifies for a broken
agent binding: retrying a misconfiguration cannot fix it, and `TOOL_ERROR` would
burn the whole retry budget re-discovering the same missing directory. It lands
in the existing execution-failure block path, whose
advertised `retry_stage` is the correct move: fix the project row with
`PUT /api/projects/{id}`, then retry. No new block kind, no `block_policy`
change, nothing frozen touched.

This is what makes §1.2 unreachable rather than merely unlikely: it holds for
rows that predate validation and for a path deleted after it was validated.

### 3.6 `GET /api/projects/{project_id}/readiness`

Per-project diagnosis, no secrets:

```json
{
  "binding": "local",
  "repo_url": "/home/me/code/app",
  "resolved_path": "/home/me/code/app",
  "exists": true,
  "is_git_repository": true,
  "default_branch": "main",
  "clean": true,
  "problem": null
}
```

For `scratch`, `binding` says so and `resolved_path` points at the
`ORCHESTRATOR_HOME` location, so "I am about to run against an empty repo I did
not choose" is readable before the run rather than after.

### 3.7 `GET /api/readiness`

One call for the setup checklist, composed from the validators that already
exist — it reimplements none of them:

```json
{
  "ok": false,
  "checks": [
    {"name": "reasoner",   "status": "ok",   "detail": "llm · openrouter · <model>"},
    {"name": "runner",     "status": "ok",   "detail": "real · 1 agent bound"},
    {"name": "binaries",   "status": "warn", "detail": "gemini not on PATH"},
    {"name": "secrets",    "status": "ok",   "detail": "master key present"},
    {"name": "catalog",    "status": "ok",   "detail": "3 capabilities · 1 agent · 1 provider/model"},
    {"name": "projects",   "status": "fail", "detail": "1 of 2 projects have an unusable repository"}
  ]
}
```

`status` is `ok | warn | fail`; `ok` at the top level is false when any check
fails. `warn` exists so a missing optional runtime binary does not read as a
broken install. **No check ever returns secret material** — the secrets check
reports presence of the master key and nothing else, and a test asserts the
serialized payload contains no `api_key`-shaped value.

### 3.8 G6 and contract hygiene

- **`POST /api/plans/{plan_id}/retry-policy`** gets the integration contract test
  it never had: apply a new budget, read it back off the plan document, 404 on
  an unknown plan, and reject a nonsense value. The route takes no version, so
  there is no 409 case to assert (`routers/plans.py:1194`).
- **The OpenAPI description** (`server.py:103-108`) still advertises "the 9-phase
  plan lifecycle (discovery, architecture, enriching, the two human gates,
  execution, the replan loop)" — the model ADR-003 superseded, and the first
  thing a preview evaluator reads. Rewritten to the cyclic lifecycle.
- **Frontend**: `subscribeToEvents` appends `?token=` when `VITE_API_TOKEN` is
  set (every other call already sends the header via `request()`), and
  `npm run generate:api` regenerates the types for the two new endpoints.

## 4. Data and migrations

None. No table, column, or domain type changes.

## 5. Testing

| Area | Test |
|---|---|
| Guard coverage | Parametrized over every operation in `app.openapi()`: 401 without a token when the env var is set; 200/expected with it. `/health` explicitly excluded and asserted open |
| Guard mechanism | `/api/events` accepts `?token=`; a representative non-events operation rejects the same query token |
| Access log | `api_start` invokes uvicorn with `access_log=False` |
| Binding validation | valid repo → 201; missing path → 422; existing dir without `.git` → 422; remote URL → 201 with no network call (asserted by patching `subprocess.run`); absent `repo_url` → 201 reported as `scratch` |
| Init guard (regression) | A project whose named repo was deleted raises on resolve instead of initializing — the direct lock on "green against nothing" |
| Project readiness | Reports each binding kind, and `problem` names the failure for a broken one |
| Consolidated readiness | Reports `fail` when the reasoner is misconfigured; payload contains no secret material |
| Retry policy | The §3.8 contract test |

Integration tests use `TestClient` and `tmp_path` git repositories, matching
`tests/integration/test_git_workspace.py`.

## 6. Files touched

```text
backend/src/api/server.py                     guard applied at include; OpenAPI description
backend/src/api/security.py                   require_api_token_or_query
backend/src/api/routers/{reference,config,reasoner,runner,metrics}.py
                                              per-router dependency removed
backend/src/api/routers/reference.py          binding validation + project readiness route
backend/src/api/routers/readiness.py          new: GET /api/readiness
backend/src/api/exceptions.py                 PROJECT_BINDING_INVALID -> 422
backend/src/infra/git/repository_binding.py   new: validate_repo_url
backend/src/infra/git/project_workspace.py    allow_init wiring
backend/src/infra/git/workspace.py            allow_init guard
backend/src/infra/cli/main.py                 access_log=False
frontend/src/lib/api.ts                       EventSource token; two new clients
frontend/src/types/generated/                 regenerated
```

## 7. Risks and rejected alternatives

**Turning the guard on breaks a client that was relying on it being off.** Both
known clients already send the token on every call — `request()` in `api.ts` and
`api.sh` in every fixture — so the exposure is to an unknown local script. The
guard is inert when `ORCHESTRATOR_API_TOKEN` is unset, which is the default and
the documented local-dev posture, so nothing changes for anyone who has not
opted in.

**Rejected: a query token accepted everywhere.** One code path, but it puts the
token in reach of any URL, Referer, or shell history for routes that have a
perfectly good header. The narrow dependency costs about six lines.

**Rejected: fetch + ReadableStream instead of EventSource.** The correct end
state — no token in any URL, one mechanism — but it reimplements EventSource's
reconnect semantics inside a Phase 4 branch, with the operator's live feed as
the blast radius. Recorded for Phase 5, which owns that file.

**Rejected: validating remote repositories at write time.** See §3.4.

**Rejected: refusing projects without a `repo_url`.** The scratch repo is a
legitimate demo path; the defect was that it was silent, not that it exists.

## 8. Exit criteria

Closes, for Phase 4:

- ✅ *Every advertised action works in the state that advertises it* — for the
  auth dimension: no operation is reachable that the guard's contract claims is
  not.
- ✅ *OpenAPI describes the cyclic lifecycle; generated frontend types are
  current.*
- ✅ *Integration tests cover each new/corrected contract.*
- ◻ *Tier 0/Tier 1 need no direct SQLite edit or hidden env fallback* — advanced
  by the readiness reads; completed once P4.2's worker health lands.

Deferred to P4.2/P4.3 by design: worker health, capacity/liveness reads,
`requires_human`, the evidence read model.
