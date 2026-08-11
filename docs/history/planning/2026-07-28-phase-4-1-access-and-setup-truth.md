# P4.1 — Access and setup truth: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the control-plane guard default-on, make a mistyped repository binding impossible to mistake for a green run, and give the setup checklist one readiness call.

**Architecture:** Three independent changes sharing one branch. (1) The API token dependency moves from five opt-in router declarations to every `include_router` call, with `/api/events` taking a query-string variant because EventSource cannot send headers. (2) Repository bindings are validated when written, and the git workspace refuses to initialize a repository that was named rather than scratch. (3) Two read-only endpoints report per-project and whole-installation readiness, composed from validators that already exist.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy, pytest (`integration` marker), structlog, TypeScript/React on the generated client.

## Global Constraints

- **No domain change and no migration.** `src/domain/` is frozen; nothing in this plan touches it.
- **Dependency rule:** `domain → app → infra & api`. New validation lives in `src/infra/git/`, called from a thin router — the same shape as `validate_reasoner_config` in `routers/reasoner.py`.
- **One error map.** New failures get a code in `src/api/exceptions.py::_STATUS_BY_CODE`; never a try/except returning a response inside a router.
- **No `print()`, no stdlib `logging`.** `log = structlog.get_logger(__name__)`, namespaced event names.
- **Never log or return secret material.** Readiness reports presence, never values.
- `mypy src` must pass with zero errors; `uv run ruff check src tests` must pass. Use `from __future__ import annotations`.
- Run backend tests with `uv run pytest` (the pinned toolchain), not a bare `pytest`.

---

### Task 1: The token guard becomes default-on

**Files:**
- Modify: `backend/src/api/server.py:127-134`
- Modify: `backend/src/api/routers/reference.py:32`, `config.py:17`, `reasoner.py:22-26`, `runner.py:26-30`, `metrics.py:20-24`
- Test: `backend/tests/integration/test_control_plane_auth.py` (create)

**Interfaces:**
- Consumes: `require_api_token` from `src/api/security.py` (unchanged in this task).
- Produces: every operation in `app.openapi()["paths"]` except `GET /health` answers 401 without a token when `ORCHESTRATOR_API_TOKEN` is set.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/integration/test_control_plane_auth.py`:

```python
"""The guard is default-on, and proven over the whole route inventory.

The Phase 3 audit found `plans.router` and `events.router` unguarded. The defect
was not two missing declarations — it was that the guard was opt-in, so any
router, including ones not yet written, was unguarded by default. Parametrizing
over `app.openapi()` rather than a hand-written list is what makes a future
router covered before it exists.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from src.api import dependencies
from src.api.server import create_app
from src.infra.container import AppContainer
from src.infra.db.tables import Base

pytestmark = pytest.mark.integration

# A liveness check that requires a secret cannot serve the setup checklist it
# exists for.
OPEN_OPERATIONS = {("GET", "/health")}


def _operations() -> list[tuple[str, str]]:
    paths = create_app().openapi()["paths"]
    return sorted(
        (method.upper(), path)
        for path, operations in paths.items()
        for method in operations
        if method.upper() in {"GET", "POST", "PUT", "DELETE"}
    )


@pytest.fixture
def guarded_client(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "sekrit")
    container = AppContainer(orchestrator_home=tmp_path)
    Base.metadata.create_all(container.engine)
    with TestClient(create_app(container)) as client:
        yield client
    dependencies.set_container(None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("method", "path"), _operations(), ids=lambda value: str(value).replace("/", "_")
)
def test_no_operation_answers_without_a_token(guarded_client, method, path):
    """No body is sent on purpose: the router-level dependency is solved before
    body validation, so a missing token must win over a missing body."""
    if (method, path) in OPEN_OPERATIONS:
        pytest.skip("deliberately open")
    url = path
    for placeholder in ("{plan_id}", "{attempt_id}", "{project_id}", "{provider_id}",
                        "{model_id}", "{agent_id}", "{capability_id}", "{scope}", "{key}"):
        url = url.replace(placeholder, "x")

    response = guarded_client.request(method, url)

    assert response.status_code == 401, (
        f"{method} {path} answered {response.status_code} without a token"
    )
    assert response.json()["error"]["code"] == "INVALID_API_TOKEN"


def test_health_stays_open(guarded_client):
    assert guarded_client.get("/health").status_code == 200
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && uv run pytest tests/integration/test_control_plane_auth.py -q --no-cov`
Expected: FAIL — roughly 35 parametrized cases answer 200/404/422 instead of 401 (every `/api/plans…` operation plus `/api/events`).

Note the error-envelope key shape while you are here: if `response.json()["error"]["code"]` raises, print one failing body and match the real envelope produced by `src/api/exceptions.py`.

- [ ] **Step 3: Remove the five per-router declarations**

In `backend/src/api/routers/reference.py:32`:

```python
router = APIRouter(tags=["reference"])
```

In `config.py:17`:

```python
router = APIRouter(prefix="/config", tags=["config"])
```

In `reasoner.py`, `runner.py`, `metrics.py`, drop the `dependencies=[Depends(require_api_token)]` line from each `APIRouter(...)` call, keeping `prefix` and `tags`. Remove the now-unused `Depends`/`require_api_token` imports only where nothing else uses them — ruff will tell you.

- [ ] **Step 4: Apply the guard at mount time**

In `backend/src/api/server.py`, replace the include block at `:127-134`:

```python
    _prefix = "/api"
    # The guard is applied HERE, once, rather than declared per router: the
    # Phase 3 audit found two routers that had simply never opted in, and an
    # opt-in guard makes that the default outcome for every future router.
    _guarded = [Depends(require_api_token)]
    app.include_router(plans.router, prefix=_prefix, dependencies=_guarded)
    app.include_router(reference.router, prefix=_prefix, dependencies=_guarded)
    app.include_router(config.router, prefix=_prefix, dependencies=_guarded)
    app.include_router(reasoner.router, prefix=_prefix, dependencies=_guarded)
    app.include_router(runner.router, prefix=_prefix, dependencies=_guarded)
    app.include_router(metrics.router, prefix=_prefix, dependencies=_guarded)
    app.include_router(events.router, prefix=_prefix, dependencies=_guarded)
```

Add the imports at the top of `server.py`:

```python
from fastapi import Depends

from src.api.security import require_api_token
```

- [ ] **Step 5: Run the test and the existing API suite**

Run: `cd backend && uv run pytest tests/integration/test_control_plane_auth.py tests/integration/test_api.py -q --no-cov`
Expected: PASS. `test_control_plane_token_guard` in `test_api.py` still passes — it asserted the guard on `GET /api/providers`, which is now guarded by a different mechanism with the same effect.

- [ ] **Step 6: Commit**

```bash
git add backend/src/api backend/tests/integration/test_control_plane_auth.py
git commit -m "fix(api): guard every router by default, not by opt-in

The Phase 3 audit found plans.router and events.router unguarded, so 36 of 64
operations answered unauthenticated callers while security.py claimed the
opposite. Two added dependencies would have left the next router free to repeat
it, so the guard moves to the include_router calls and the test parametrizes
over the OpenAPI inventory rather than a list."
```

---

### Task 2: `/api/events` accepts the token in the query string

**Files:**
- Modify: `backend/src/api/security.py`
- Modify: `backend/src/api/server.py` (events include line from Task 1)
- Modify: `frontend/src/lib/api.ts:556-603`
- Test: `backend/tests/integration/test_control_plane_auth.py` (append)

**Interfaces:**
- Consumes: `require_api_token` (Task 1).
- Produces: `require_api_token_or_query(authorization, x_api_token, token)` in `src/api/security.py`, used **only** by the events router.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/integration/test_control_plane_auth.py`:

```python
def test_the_event_stream_accepts_a_query_token(guarded_client):
    """EventSource cannot set headers (frontend/src/lib/api.ts), so the one
    route a browser opens directly takes the token in the query string."""
    denied = guarded_client.get("/api/events", params={"token": "wrong"})
    assert denied.status_code == 401

    # A correct token must NOT be rejected. The stream never ends on its own, so
    # assert on the rejection boundary rather than opening it here; the live
    # streaming path is covered by tests/integration/test_sse_stream.py.
    accepted = guarded_client.get(
        "/api/events", params={"token": "sekrit"}, headers={"X-Stop": "1"}
    )
    assert accepted.status_code != 401


def test_no_other_route_accepts_a_query_token(guarded_client):
    """Confining the URL-borne token to the stream is the point: everywhere a
    header works, a header is required."""
    response = guarded_client.get("/api/providers", params={"token": "sekrit"})
    assert response.status_code == 401
```

> If `test_the_event_stream_accepts_a_query_token`'s second call blocks, delete
> those three lines and keep only the 401 assertion plus the new
> `test_sse_stream.py` case in Step 5 — an infinite stream must never be opened
> from `TestClient`.

- [ ] **Step 2: Run and watch them fail**

Run: `cd backend && uv run pytest tests/integration/test_control_plane_auth.py -q --no-cov -k query_token`
Expected: FAIL — the events route rejects every query token (Task 1 made it header-only).

- [ ] **Step 3: Add the query-capable dependency**

In `backend/src/api/security.py`, add below `require_api_token`:

```python
def require_api_token_or_query(
    authorization: str | None = Header(default=None),
    x_api_token: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> None:
    """Header auth, plus `?token=` for the ONE route a browser opens directly.

    `EventSource` cannot set request headers, so the SSE stream would otherwise
    be unguardable without inventing a session cookie. Confining the URL-borne
    token here keeps the tradeoff visible and testable — no other operation
    accepts it. `api start` runs uvicorn with `access_log=False` so the query
    string is never written down; the structured request log records the path
    only (`middleware/request_logging.py`).
    """
    expected = os.environ.get(API_TOKEN_ENV, "").strip()
    if not expected:
        return  # open in local dev
    provided = x_api_token
    if not provided and authorization and authorization.startswith("Bearer "):
        provided = authorization[len("Bearer ") :].strip()
    if not provided:
        provided = token
    if provided != expected:
        raise UnauthorizedError("Missing or invalid API token", code="INVALID_API_TOKEN")
```

Update the import line to `from fastapi import Header, Query`.

- [ ] **Step 4: Point the events router at it**

In `backend/src/api/server.py`, change only the events include:

```python
    app.include_router(
        events.router, prefix=_prefix, dependencies=[Depends(require_api_token_or_query)]
    )
```

and extend the import: `from src.api.security import require_api_token, require_api_token_or_query`.

- [ ] **Step 5: Cover the real streaming path**

In `backend/tests/integration/test_sse_stream.py`, add after the existing tests:

```python
def test_the_stream_opens_with_a_query_token(tmp_path, monkeypatch):
    """The guarded stream, opened the way a browser opens it."""
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "sekrit")
    # Reuse the live_server fixture body by requesting it after the env var is
    # set: parametrize by calling the fixture through `request.getfixturevalue`.
```

Simpler and preferred: give `live_server` an opt-in token by reading
`os.environ.get("ORCHESTRATOR_API_TOKEN")` instead of deleting it, and add a
test that sets the env var before requesting the fixture via
`request.getfixturevalue("live_server")`. Then:

```python
    with httpx.Client(timeout=_TIMEOUT) as client:
        with client.stream("GET", f"{base_url}/api/events?token=sekrit") as response:
            assert response.status_code == 200
        with client.stream("GET", f"{base_url}/api/events") as response:
            assert response.status_code == 401
```

- [ ] **Step 6: Keep the frontend feed alive**

In `frontend/src/lib/api.ts`, inside `subscribeToEvents`'s `connect()`:

```typescript
  function connect() {
    // EventSource cannot send headers, so the token travels as a query
    // parameter — the only route where it does. See src/api/security.py.
    const query = API_TOKEN ? `?token=${encodeURIComponent(API_TOKEN)}` : "";
    es = new EventSource(`${BASE}/api/events${query}`);
```

- [ ] **Step 7: Run everything and commit**

Run: `cd backend && uv run pytest tests/integration/test_control_plane_auth.py tests/integration/test_sse_stream.py -q --no-cov`
Then: `cd frontend && npx tsc --noEmit`
Expected: PASS, clean.

```bash
git add backend/src/api backend/tests frontend/src/lib/api.ts
git commit -m "feat(api): guard the event stream with a query-string token

EventSource cannot send headers, so the one route a browser opens directly
takes ?token= through a dependency used nowhere else — a companion test asserts
no other operation accepts it."
```

---

### Task 3: Stop uvicorn writing the token down

**Files:**
- Modify: `backend/src/infra/cli/main.py:76-82`
- Test: `backend/tests/unit/test_api_start_does_not_log_requests.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `api start` runs uvicorn with `access_log=False`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_api_start_does_not_log_requests.py`:

```python
"""A query-string token is only dangerous where it is written down.

uvicorn's access logger would print `GET /api/events?token=… HTTP/1.1` on every
reconnect. RequestLoggingMiddleware already records each request structurally
with a correlation id, status and duration — and records `request.url.path`,
never the query string — so turning uvicorn's logger off loses nothing and
removes the only place the token would land. It also retires a stdlib-logging
path the project's observability rules forbid.
"""

from __future__ import annotations

from click.testing import CliRunner

from src.infra.cli.main import cli


def test_api_start_disables_the_uvicorn_access_log(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run(app, **kwargs):
        captured.update(kwargs)

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setattr("src.api.server.create_app", lambda: object())

    result = CliRunner().invoke(cli, ["api", "start", "--port", "9999"])

    assert result.exit_code == 0, result.output
    assert captured["access_log"] is False
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && uv run pytest tests/unit/test_api_start_does_not_log_requests.py -q --no-cov`
Expected: FAIL — `KeyError: 'access_log'`.

- [ ] **Step 3: Turn it off**

In `backend/src/infra/cli/main.py`, in `api_start`:

```python
    # No uvicorn access log: RequestLoggingMiddleware already records every
    # request structurally (path only — never the query string), and the SSE
    # route carries the API token in its query string. See src/api/security.py.
    uvicorn.run(create_app(), host=host, port=port, access_log=False)
```

- [ ] **Step 4: Run and commit**

Run: `cd backend && uv run pytest tests/unit/test_api_start_does_not_log_requests.py -q --no-cov`
Expected: PASS

```bash
git add backend/src/infra/cli/main.py backend/tests/unit/test_api_start_does_not_log_requests.py
git commit -m "fix(cli): disable uvicorn's access log

It is the only place the SSE query token would be written down, and it is a
stdlib-logging path the project's observability rules already forbid. The
structured middleware logs the same request without the query string."
```

---

### Task 4: Validate a repository binding when it is written

**Files:**
- Create: `backend/src/infra/git/repository_binding.py`
- Modify: `backend/src/infra/errors.py`
- Modify: `backend/src/api/exceptions.py` (`_STATUS_BY_CODE`)
- Modify: `backend/src/api/routers/reference.py:282-310`
- Test: `backend/tests/integration/test_repository_binding.py` (create)

**Interfaces:**
- Consumes: `ProjectDefinition` (`src/domain/entities/project_definition.py`).
- Produces:
  - `RepositoryBinding(kind: Literal["local","remote","scratch"], resolved_path: str | None, default_branch: str | None)` — frozen dataclass.
  - `validate_repo_url(repo_url: str | None) -> RepositoryBinding`, raising `ProjectBindingInvalidError`.
  - `ProjectBindingInvalidError` with `code = "PROJECT_BINDING_INVALID"` → HTTP 422.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/integration/test_repository_binding.py`:

```python
"""A project's repository binding is checked when it is written.

Before this, `repo_url` was stored unvalidated and a typo did not fail: the
resolver reported default branch "main" for a path with no .git
(project_workspace.py:66) and the workspace then created, git-init'ed and
committed into it (workspace.py:150-153) — so the plan published green against
an empty repository.
"""

from __future__ import annotations

import subprocess

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from src.api import dependencies
from src.api.server import create_app
from src.infra.container import AppContainer
from src.infra.db.tables import Base

pytestmark = pytest.mark.integration


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("ORCHESTRATOR_API_TOKEN", raising=False)
    container = AppContainer(orchestrator_home=tmp_path / "home")
    Base.metadata.create_all(container.engine)
    with TestClient(create_app(container)) as test_client:
        yield test_client
    dependencies.set_container(None)  # type: ignore[arg-type]


def _git_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "checkout", "-B", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "--allow-empty", "-m", "init"], check=True, capture_output=True)
    return path


def test_a_real_repository_is_accepted(client, tmp_path):
    repo = _git_repo(tmp_path / "repo")

    response = client.post("/api/projects", json={"name": "P", "repo_url": str(repo)})

    assert response.status_code == 201


def test_a_path_that_does_not_exist_is_refused(client, tmp_path):
    response = client.post(
        "/api/projects", json={"name": "P", "repo_url": str(tmp_path / "nope")}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PROJECT_BINDING_INVALID"
    assert "does not exist" in response.json()["error"]["message"]


def test_a_directory_that_is_not_a_repository_is_refused(client, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()

    response = client.post("/api/projects", json={"name": "P", "repo_url": str(plain)})

    assert response.status_code == 422
    assert "not a git repository" in response.json()["error"]["message"]


def test_a_remote_url_is_accepted_without_touching_the_network(client, monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("write-time validation must not run git")

    monkeypatch.setattr(subprocess, "run", explode)

    response = client.post(
        "/api/projects",
        json={"name": "P", "repo_url": "https://github.com/example/repo.git"},
    )

    assert response.status_code == 201


def test_a_project_without_a_repo_url_is_still_legal(client):
    response = client.post("/api/projects", json={"name": "P"})

    assert response.status_code == 201


def test_an_update_is_validated_too(client, tmp_path):
    created = client.post("/api/projects", json={"name": "P"}).json()

    response = client.put(
        f"/api/projects/{created['id']}",
        json={"name": "P", "repo_url": str(tmp_path / "nope")},
    )

    assert response.status_code == 422
```

- [ ] **Step 2: Run and watch them fail**

Run: `cd backend && uv run pytest tests/integration/test_repository_binding.py -q --no-cov`
Expected: FAIL — every refusal case returns 201.

- [ ] **Step 3: Add the error class and its status mapping**

In `backend/src/infra/errors.py`, beside `SecretNotFoundError`:

```python
class ProjectBindingInvalidError(InfrastructureError):
    """A project names a repository that cannot be used as one."""

    code = "PROJECT_BINDING_INVALID"
```

In `backend/src/api/exceptions.py`, in the 422 block beside `REASONER_CONFIG_INVALID`:

```python
    "PROJECT_BINDING_INVALID": 422,
```

- [ ] **Step 4: Write the validator**

Create `backend/src/infra/git/repository_binding.py`:

```python
"""What a project's `repo_url` must be for a plan to run against it.

Write-time validation, called by the projects router exactly as
`routers/reasoner.py` calls `validate_reasoner_config`. Remote URLs are checked
for syntax only: a create request must not block on a slow or unreachable host,
and a repository reachable now may not be at execution time, so a network probe
would cost a timeout and buy very little.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlparse

import structlog

from src.infra.errors import ProjectBindingInvalidError

log = structlog.get_logger(__name__)

BindingKind = Literal["local", "remote", "scratch"]


@dataclass(frozen=True)
class RepositoryBinding:
    kind: BindingKind
    resolved_path: str | None
    default_branch: str | None


def _local_path(repo_url: str) -> Path | None:
    parsed = urlparse(repo_url)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path))
    if parsed.scheme == "":
        return Path(repo_url).expanduser().resolve()
    return None


def _default_branch(repo: Path) -> str | None:
    for args in (
        ["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
        ["symbolic-ref", "--quiet", "--short", "HEAD"],
    ):
        result = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().removeprefix("origin/")
    return None


def validate_repo_url(repo_url: str | None) -> RepositoryBinding:
    if not repo_url or not repo_url.strip():
        return RepositoryBinding(kind="scratch", resolved_path=None, default_branch=None)

    path = _local_path(repo_url)
    if path is None:
        return RepositoryBinding(kind="remote", resolved_path=None, default_branch=None)

    if not path.exists():
        raise ProjectBindingInvalidError(
            f"repository path {path} does not exist; a plan bound to it would run "
            "against a newly created empty repository"
        )
    if not (path / ".git").exists():
        raise ProjectBindingInvalidError(f"{path} is not a git repository")
    branch = _default_branch(path)
    if branch is None:
        raise ProjectBindingInvalidError(
            f"cannot determine the default branch of {path}; it has no HEAD and no branches"
        )
    return RepositoryBinding(kind="local", resolved_path=str(path), default_branch=branch)
```

- [ ] **Step 5: Call it from the projects router**

In `backend/src/api/routers/reference.py`, add the import and validate in both writes:

```python
from src.infra.git.repository_binding import validate_repo_url


@router.post("/projects", status_code=201)
def create_project(
    body: ProjectBody, container: AppContainer = Depends(get_container)
) -> ProjectDefinition:
    validate_repo_url(body.repo_url)
    project = ProjectDefinition(id=new_id(), name=body.name, repo_url=body.repo_url)
    container.project_repo.add(project)
    return project


@router.put("/projects/{project_id}", status_code=204)
def update_project(
    project_id: str, body: ProjectBody, container: AppContainer = Depends(get_container)
) -> None:
    validate_repo_url(body.repo_url)
    container.project_repo.update(
        ProjectDefinition(id=project_id, name=body.name, repo_url=body.repo_url)
    )
```

- [ ] **Step 6: Run and commit**

Run: `cd backend && uv run pytest tests/integration/test_repository_binding.py tests/integration/test_api.py -q --no-cov`
Expected: PASS. If a pre-existing test creates a project with a fabricated `repo_url`, fix the test to use a real `tmp_path` repo — that test was asserting the defect.

```bash
git add backend/src backend/tests/integration/test_repository_binding.py
git commit -m "feat(api): validate a project's repository binding when it is written

A local repo_url must exist, be a git repository, and have a determinable
default branch. Remote URLs are syntax-checked only — no clone, no network call
at write time."
```

---

### Task 5: Never initialize a repository that was named

**Files:**
- Modify: `backend/src/infra/git/workspace.py:78-80`, `:148-153`
- Modify: `backend/src/infra/git/project_workspace.py:34-62`
- Test: `backend/tests/integration/test_git_workspace.py` (append)

**Interfaces:**
- Consumes: `RepositoryBinding` semantics from Task 4 (`scratch` = no `repo_url`).
- Produces: `GitBranchWorkspace(repo_dir, default_branch="main", allow_init=True)`; with `allow_init=False`, `_ensure_repo` raises `TaskFailed(..., FailureKind.AUTH_ERROR)`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/integration/test_git_workspace.py`:

```python
@pytest.mark.asyncio
async def test_a_named_repository_is_never_created(tmp_path):
    """The regression lock on "green against nothing".

    A project that NAMES a repository has made a claim about the world. If the
    path is wrong — a typo, a rename, a directory deleted after binding — the
    workspace used to create it, git-init it and commit into it, and the plan
    published green against an empty repository. Only a scratch project (no
    repo_url) may be created on demand.
    """
    missing = tmp_path / "not-there"
    workspace = GitBranchWorkspace(missing, default_branch="main", allow_init=False)

    with pytest.raises(TaskFailed) as caught:
        await workspace.begin("plan-1", "task-1", 1)

    assert caught.value.kind is FailureKind.AUTH_ERROR
    assert not missing.exists(), "a named repository must never be created"


@pytest.mark.asyncio
async def test_a_scratch_repository_is_still_created(tmp_path):
    scratch = tmp_path / "scratch"
    workspace = GitBranchWorkspace(scratch, default_branch="main", allow_init=True)

    handle = await workspace.begin("plan-1", "task-1", 1)

    assert (scratch / ".git").exists()
    await workspace.discard(handle)
```

Match the existing file's import list and the real signature of `begin` — read
the neighbouring tests in that file first and copy their call shape, including
whether `begin` takes keyword-only `cycle_id`/`goal_id`/`run_id`/`base_ref`.

- [ ] **Step 2: Run and watch the first test fail**

Run: `cd backend && uv run pytest tests/integration/test_git_workspace.py -q --no-cov -k named_repository`
Expected: FAIL — `TypeError: unexpected keyword argument 'allow_init'`.

- [ ] **Step 3: Add the flag and the guard**

In `backend/src/infra/git/workspace.py`:

```python
    def __init__(
        self, repo_dir: Path, default_branch: str = "main", allow_init: bool = True
    ) -> None:
        self._repo = Path(repo_dir)
        self._default_branch = default_branch
        # A project that NAMES a repository has made a claim about the world;
        # creating one silently turns a typo into a green run against nothing.
        # Only a scratch project (no repo_url) may be created on demand.
        self._allow_init = allow_init
```

and in `_ensure_repo`:

```python
    def _ensure_repo(self) -> None:
        if (self._repo / ".git").exists():
            return
        if not self._allow_init:
            raise TaskFailed(
                f"repository {self._repo} does not exist or is not a git repository; "
                "fix the project's repo_url and retry",
                FailureKind.AUTH_ERROR,
            )
        self._repo.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", str(self._repo)], check=True, capture_output=True)
        _git(self._repo, "checkout", "-B", self._default_branch)
        _git(self._repo, "commit", "--allow-empty", "-m", "chore: initial commit")
        log.info("workspace.repo_seeded", repo=str(self._repo))
```

`AUTH_ERROR` rather than `TOOL_ERROR` on purpose: it is the terminal, non-retryable kind, matching the treatment CLAUDE.md specifies for a broken agent binding. `TOOL_ERROR` would spend the whole retry budget rediscovering the same missing directory.

- [ ] **Step 4: Wire it from the project resolver**

In `backend/src/infra/git/project_workspace.py`, in both `resolve` and `workspaces`:

```python
        workspace = GitBranchWorkspace(
            repo, default_branch=default_branch, allow_init=project.repo_url is None
        )
```

- [ ] **Step 5: Run the git and execution suites**

Run: `cd backend && uv run pytest tests/integration/test_git_workspace.py tests/integration/test_drive_plan_sqlite_git.py -q --no-cov`
Expected: PASS. Fixtures that build a workspace directly keep the default `allow_init=True`.

- [ ] **Step 6: Commit**

```bash
git add backend/src/infra/git backend/tests/integration/test_git_workspace.py
git commit -m "fix(workspace): never git-init a repository a project named

A missing path used to be created, initialized and committed into, so a typo in
repo_url published green against an empty repository. Only a scratch project
(no repo_url) may be created on demand; a named one fails terminally."
```

---

### Task 6: Readiness reads

**Files:**
- Create: `backend/src/api/routers/readiness.py`
- Modify: `backend/src/api/routers/reference.py` (project readiness route)
- Modify: `backend/src/api/server.py` (include the new router)
- Test: `backend/tests/integration/test_readiness.py` (create)

**Interfaces:**
- Consumes: `validate_repo_url` (Task 4); `validate_reasoner_config` (`src/infra/reasoner/factory.py`); `validate_agent_runner_mode`, `validate_agent_binding` (`src/infra/runtime/factory.py`); `check_dependencies` (`src/infra/runtime/dependency_checker.py`).
- Produces: `GET /api/readiness` → `ReadinessResponse{ok: bool, checks: list[ReadinessCheck]}` where `ReadinessCheck{name: str, status: "ok"|"warn"|"fail", detail: str}`; `GET /api/projects/{project_id}/readiness` → `ProjectReadinessResponse`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/integration/test_readiness.py`:

```python
"""One call for the setup checklist, and one per project.

Before this, "can this machine run a plan?" was assembled by hand from
/api/reasoner/status and /api/runner/status plus operator inference, and the
first symptom of an incomplete setup was a failed run.
"""

from __future__ import annotations

import json
import subprocess

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from src.api import dependencies
from src.api.server import create_app
from src.infra.container import AppContainer
from src.infra.db.tables import Base

pytestmark = pytest.mark.integration


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("ORCHESTRATOR_API_TOKEN", raising=False)
    container = AppContainer(orchestrator_home=tmp_path / "home")
    Base.metadata.create_all(container.engine)
    with TestClient(create_app(container)) as test_client:
        yield test_client
    dependencies.set_container(None)  # type: ignore[arg-type]


def test_readiness_names_every_check(client):
    body = client.get("/api/readiness").json()

    assert {check["name"] for check in body["checks"]} == {
        "reasoner", "runner", "binaries", "secrets", "catalog", "projects",
    }
    assert all(check["status"] in {"ok", "warn", "fail"} for check in body["checks"])


def test_an_empty_installation_is_not_ready(client):
    body = client.get("/api/readiness").json()

    assert body["ok"] is False
    catalog = next(c for c in body["checks"] if c["name"] == "catalog")
    assert catalog["status"] == "fail"


def test_readiness_never_returns_secret_material(client, tmp_path):
    client.post(
        "/api/providers",
        json={"name": "p", "base_url": "https://api.example.com", "api_key": "sk-secret-value"},
    )

    payload = json.dumps(client.get("/api/readiness").json())

    assert "sk-secret-value" not in payload


def test_project_readiness_reports_a_broken_binding(client, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "-B", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "--allow-empty", "-m", "init"], check=True, capture_output=True)
    project = client.post("/api/projects", json={"name": "P", "repo_url": str(repo)}).json()

    body = client.get(f"/api/projects/{project['id']}/readiness").json()
    assert body["binding"] == "local"
    assert body["is_git_repository"] is True
    assert body["problem"] is None

    # The directory disappears after a valid binding — the case write-time
    # validation cannot catch.
    subprocess.run(["rm", "-rf", str(repo)], check=True)

    body = client.get(f"/api/projects/{project['id']}/readiness").json()
    assert body["exists"] is False
    assert body["problem"] is not None


def test_a_scratch_project_says_so(client):
    project = client.post("/api/projects", json={"name": "P"}).json()

    body = client.get(f"/api/projects/{project['id']}/readiness").json()

    assert body["binding"] == "scratch"
    assert body["resolved_path"] is not None
```

- [ ] **Step 2: Run and watch them fail**

Run: `cd backend && uv run pytest tests/integration/test_readiness.py -q --no-cov`
Expected: FAIL — 404 on both routes.

- [ ] **Step 3: Write the readiness router**

Create `backend/src/api/routers/readiness.py`:

```python
"""One call that answers "can this machine run a plan?".

Composed from the validators that already serve /api/reasoner/status and
/api/runner/status — it reimplements none of them. `warn` exists so a missing
optional runtime binary does not read as a broken install. No check ever
returns secret material: the secrets check reports the presence of the master
key and nothing else.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.dependencies import get_container
from src.infra.container import AppContainer
from src.infra.git.repository_binding import validate_repo_url
from src.infra.errors import ProjectBindingInvalidError

router = APIRouter(tags=["readiness"])

Status = Literal["ok", "warn", "fail"]


class ReadinessCheck(BaseModel):
    name: str
    status: Status
    detail: str


class ReadinessResponse(BaseModel):
    ok: bool
    checks: list[ReadinessCheck]


@router.get("/readiness", response_model=ReadinessResponse)
def readiness(container: AppContainer = Depends(get_container)) -> ReadinessResponse:
    checks = [
        _reasoner(container),
        _runner(container),
        _binaries(),
        _secrets(container),
        _catalog(container),
        _projects(container),
    ]
    return ReadinessResponse(
        ok=all(check.status != "fail" for check in checks), checks=checks
    )
```

Then one small function per check. Each wraps the existing validator in
`try/except` on its declared error type and maps it to a `ReadinessCheck` — this
is the one place a broad catch is correct, because a readiness probe that raises
is useless. Read `backend/src/api/routers/reasoner.py` and `runner.py` first and
call exactly what they call:

- `_reasoner`: `validate_reasoner_config(container)` → `ok`, or `fail` with the
  error message; if `reasoner.mode` is `stub`, `ok` with detail `"stub"`.
- `_runner`: `validate_agent_runner_mode(container)` plus per-agent
  `validate_agent_binding`; `fail` if the mode is `real` and any bound agent is
  broken, `ok` for `dry-run`.
- `_binaries`: `check_dependencies()` → `warn` when a probe is missing, `ok`
  when all present. Never `fail`: an unused runtime's absence is not a broken
  install.
- `_secrets`: `ok` when `ORCHESTRATOR_MASTER_KEY` is set, `fail` otherwise, with
  detail `"master key present"` / `"ORCHESTRATOR_MASTER_KEY is not set"`. Report
  presence only.
- `_catalog`: counts from `container.capability_repo.list()`,
  `agent_repo.list()`, `provider_repo.list()`, `model_repo.list()`; `fail` when
  there is no agent or no provider/model pair, detail
  `"{n} capabilities · {n} agents · {n} provider/model"`.
- `_projects`: for each `container.project_repo.list()`, call
  `validate_repo_url(project.repo_url)` and catch
  `ProjectBindingInvalidError`; `fail` with `"{bad} of {total} projects have an
  unusable repository"` when any fails, `ok` otherwise, and `ok` with
  `"no projects yet"` when the list is empty.

- [ ] **Step 4: Add the per-project route**

In `backend/src/api/routers/reference.py`, below `update_project`:

```python
class ProjectReadinessResponse(BaseModel):
    binding: str
    repo_url: str | None
    resolved_path: str | None
    exists: bool
    is_git_repository: bool
    default_branch: str | None
    problem: str | None


@router.get("/projects/{project_id}/readiness", response_model=ProjectReadinessResponse)
def project_readiness(
    project_id: str, container: AppContainer = Depends(get_container)
) -> ProjectReadinessResponse:
    """Diagnose one project's repository without running a plan against it.

    Write-time validation cannot cover a path that disappears afterwards, so
    this re-checks the live filesystem every call.
    """
    project = container.project_repo.get(project_id)
    resolved = container.project_workspace_resolver_path(project)  # see note
    ...
```

Note: resolve the scratch path the same way `ProjectWorkspaceResolver._repository_path` does (`$ORCHESTRATOR_HOME/projects/<id>/repo`). If exposing that requires a public helper, add `repository_path_for(project) -> Path` to `project_workspace.py` and call it from both places rather than duplicating the rule.

- [ ] **Step 5: Mount the router**

In `backend/src/api/server.py`, beside the other guarded includes:

```python
    app.include_router(readiness.router, prefix=_prefix, dependencies=_guarded)
```

and import `readiness` with the other routers.

- [ ] **Step 6: Run and commit**

Run: `cd backend && uv run pytest tests/integration/test_readiness.py tests/integration/test_control_plane_auth.py -q --no-cov`
Expected: PASS — including the auth sweep, which now covers the two new operations automatically.

```bash
git add backend/src backend/tests/integration/test_readiness.py
git commit -m "feat(api): readiness reads for the installation and per project

One call for the setup checklist, composed from the existing reasoner, runner,
binary and catalog validators, plus a per-project repository diagnosis that
re-checks the live filesystem — the case write-time validation cannot cover."
```

---

### Task 7: The retry-policy contract, the OpenAPI description, and regenerated types

**Files:**
- Modify: `backend/src/api/server.py:100-111`
- Test: `backend/tests/integration/test_api.py` (append)
- Modify: `frontend/src/types/generated/` (regenerated)
- Modify: `docs/architecture/capability-matrix.md`

**Interfaces:**
- Consumes: everything above.
- Produces: no new runtime surface; closes G6 and the OpenAPI exit criterion.

- [ ] **Step 1: Write the failing contract test**

Append to `backend/tests/integration/test_api.py`:

```python
def test_retry_policy_can_be_retuned_over_http(client):
    """The one route the Phase 3 audit found with no test exercising it.

    There is no version parameter on this route, so there is no 409 case — the
    contract is: it applies, it reads back, and it refuses nonsense.
    """
    project_id = client.post("/api/projects", json={"name": "P"}).json()["id"]
    plan_id = client.post(
        "/api/plans", json={"brief": "goal: G\ntask: t", "project_id": project_id}
    ).json()["plan_id"]

    response = client.post(
        f"/api/plans/{plan_id}/retry-policy", json={"max_attempts": 9}
    )
    assert response.status_code == 204

    assert client.post(
        "/api/plans/ghost/retry-policy", json={"max_attempts": 9}
    ).status_code == 404

    assert client.post(
        f"/api/plans/{plan_id}/retry-policy", json={"max_attempts": -1}
    ).status_code == 422
```

- [ ] **Step 2: Run it**

Run: `cd backend && uv run pytest tests/integration/test_api.py -q --no-cov -k retry_policy`
Expected: the 204 and 404 cases pass; the `-1` case tells you whether validation exists. If it returns 204, add a `ge=1` constraint to the matching field on `RetryPolicyUpdateRequest` in `routers/plans.py` and re-run — a budget of -1 is not a policy.

- [ ] **Step 3: Rewrite the OpenAPI description**

In `backend/src/api/server.py`, replace the `description=` argument:

```python
        description=(
            "RESTful API for the Praxis Orchestrator: the cyclic project-plan "
            "lifecycle (intent, cycle architecture, JIT goal enrichment, "
            "execution, publication) behind human review gates, reference-data "
            "catalogs, two-tier config, readiness reads, and the live SSE "
            "event stream. The nine-phase `phase` field is a compatibility "
            "projection for migrated plans, never the authority for a plan "
            "with an active cycle."
        ),
```

- [ ] **Step 4: Regenerate the frontend types**

Run: `cd frontend && npm run generate:api && npx tsc --noEmit && npm run build`
Expected: clean. The generated directory gains the two readiness operations.

- [ ] **Step 5: Update the matrix rows this branch changed**

In `docs/architecture/capability-matrix.md`: mark G1, G6 and G11 closed (delete the gap sections and their `([G…](#g…))` markers, per the repo's rule that a fixed known issue is deleted and replaced by the test that locks it), add rows for the two readiness operations, and change the "Repository / workspace readiness" row from `hidden` to `full`. Delete the "Control-plane exposure" section's first two bullets from `docs/architecture/known-issues.md` — G7 stays, it is Phase 5's.

The matrix completeness test will fail until the new operations are listed. That is the test doing its job.

- [ ] **Step 6: Full verification and commit**

```bash
cd backend && uv run ruff check src tests && uv run mypy src \
  && uv run pytest -m "not integration" -q --no-cov \
  && uv run pytest -m integration -q --no-cov
cd ../frontend && npx tsc --noEmit && npm run build
```

Expected: all clean.

```bash
git add -A
git commit -m "test(api): cover the retry-policy contract; describe the cyclic lifecycle

Closes the audit's G6, replaces an OpenAPI description that still advertised the
nine-phase machine ADR-003 superseded, and regenerates the client types."
```

---

## Self-review

**Spec coverage.** §3.1 → Task 1. §3.2 → Task 2. §3.3 → Task 3. §3.4 → Task 4. §3.5 → Task 5. §3.6 and §3.7 → Task 6. §3.8 → Task 7. §5's test table maps one row per task. No spec section is unimplemented.

**Known soft spots, called out rather than hidden.** Task 5 Step 1 tells the implementer to read the neighbouring tests before copying a `begin()` call shape, because that signature takes keyword-only arguments this plan does not reproduce in full. Task 6 Step 4 flags that the scratch-path rule lives in a private method and must be shared rather than duplicated. Task 2 Step 5 offers a fixture change rather than pretending the existing `live_server` fixture already supports a token. Task 7 Step 2 branches on whether validation already exists.

**Type consistency.** `RepositoryBinding`, `validate_repo_url`, `ProjectBindingInvalidError`/`PROJECT_BINDING_INVALID`, `allow_init`, `ReadinessCheck`/`ReadinessResponse` are named identically everywhere they appear.
