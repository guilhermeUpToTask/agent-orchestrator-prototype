# P8.1 — Repository Choice and Forge Publication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the operator name their repository binding explicitly, prove a remote works at setup instead of mid-cycle, and make `open_pr` actually push a branch and open a GitHub pull request so `output_reference` is a fact rather than a typed claim.

**Architecture:** A new `ForgePort` in `app/` (beside `sandbox_port.py`, never seen by the frozen domain) with a `GitHubForge` adapter and a `NoForge` permanent fallback. The forge binding lives in the existing project-scoped config store and the token in the existing secret store, so no domain entity changes and no un-freeze is needed. The push and the GitHub API call happen in a new `publish_cycle` use case **outside** any transaction; only after the PR exists does it re-read the plan, re-guard the gate, and record the disposition.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy/SQLite, Pydantic v2, `httpx` (promoted to an explicit runtime dependency), pytest + pytest-xdist, React 19 / TypeScript / Vite, vitest + Testing Library.

**Design spec:** [`docs/superpowers/specs/2026-08-02-phase-8-1-repository-choice-and-forge-publication-design.md`](../specs/2026-08-02-phase-8-1-repository-choice-and-forge-publication-design.md)

## Global Constraints

- **The frozen domain is not touched.** No file under `agent_orchestrator/domain/` is created or modified by any task in this plan. If a task seems to need one, stop and escalate — it means the design was wrong, and a domain change needs a `docs/decisions/decision-log.md` entry and an explicit un-freeze.
- **Dependency rule:** `domain` → `app` → `infra` & `api`. Never import `app`, `infra`, or `api` inside `domain/`. Never import `infra` inside `app/`.
- **The orchestrator opens a pull request and never merges one.** `OutputDisposition.MERGE` stays a recorded human claim.
- **Only `cycle/<id>` is ever pushed.** No task may push, write, or force-update the default branch.
- **Side effects never run inside a `with uow:` transaction** (architectural invariant #5).
- **Routers stay thin** (invariant #8): no `try/except` returning HTTP responses inside a router, no blanket `KeyError`/`ValueError` handlers. Error codes go in the ONE table, `agent_orchestrator/api/exceptions.py::_STATUS_BY_CODE`.
- **Never log a token.** Secrets live envelope-encrypted; `resolve()` in `infra/db/secret_store.py` stays the single decryption point in the codebase.
- **No `print()` and no stdlib `logging`.** Use `log = structlog.get_logger(__name__)` with namespaced, action-oriented event names.
- **Every module uses `from __future__ import annotations`.**
- **Quality gates that must pass before every commit** (run from `backend/`):
  - `ruff check agent_orchestrator tests --fix`
  - `mypy agent_orchestrator` — zero errors, no excludes
- **GitHub only.** Do not add a GitLab/Gitea/Bitbucket adapter, a provider-detection heuristic, or a `forge.provider` value other than `github`.
- **New routes must be added to `docs/architecture/capability-matrix.md`** or `backend/tests/unit/test_capability_matrix.py` fails the build in both directions.
- The API token guard (`require_api_token`) is applied at router mount; do not add per-route auth.

---

## File Structure

**Backend — created**

| File | Responsibility |
|---|---|
| `agent_orchestrator/app/forge_port.py` | The `ForgePort` protocol, `PullRequestRef`, and `ForgeNotConfiguredError`. No I/O. |
| `agent_orchestrator/infra/forge/__init__.py` | Package marker. |
| `agent_orchestrator/infra/forge/no_forge.py` | `NoForge` — the permanent fallback adapter. |
| `agent_orchestrator/infra/forge/github.py` | `GitHubForge` — REST against `api.github.com`, plus `verify_github_token`. |
| `agent_orchestrator/infra/forge/binding.py` | Reads/writes the three project-scoped config keys and the token ref. The one place the key names live. |
| `agent_orchestrator/app/use_cases/publish_cycle.py` | The three-step publication flow. |
| `agent_orchestrator/app/pr_body.py` | Renders the evidence document into a PR body. Pure function, no I/O. |

**Backend — modified**

| File | Change |
|---|---|
| `agent_orchestrator/infra/git/repository_binding.py` | `GIT_NONINTERACTIVE_ENV`, `probe_remote()`, `RemoteProbe`. |
| `agent_orchestrator/infra/git/project_workspace.py:126` | `_materialize_remote` runs the clone under `GIT_NONINTERACTIVE_ENV`. |
| `agent_orchestrator/api/routers/reference.py` | `binding` on `ProjectBody`; `/projects/probe`, `/projects/{id}/clone`, `/projects/{id}/forge` (GET/PUT/DELETE). |
| `agent_orchestrator/api/routers/plans.py:1194` | Publication route calls `publish_cycle`. |
| `agent_orchestrator/api/exceptions.py` | Five new codes. |
| `agent_orchestrator/infra/errors.py` | Five new error classes. |
| `agent_orchestrator/infra/container.py` | `forge_for(project_id)` factory. |
| `agent_orchestrator/infra/db/secret_ref.py` | `SecretRef.for_forge`. |
| `agent_orchestrator/app/testing/fakes.py` | `FakeForge`. |
| `pyproject.toml` | `httpx` as an explicit runtime dependency. |

**Frontend — modified**

| File | Change |
|---|---|
| `frontend/src/views/settings/ProjectsSection.tsx` | `ProjectDialog` → four-step `ProjectWizard`. |
| `frontend/src/lib/queries.ts` | `useProbeRepository`, `useCloneProject`, `useForgeBinding`, `useSetForgeBinding`. |
| `frontend/src/types/generated/` | Regenerated from the OpenAPI schema. |

---

## Task 1: Stop a private clone from hanging the worker (F2)

Standalone defect fix in current `main`; ships value with nothing else.

**Files:**
- Modify: `agent_orchestrator/infra/git/repository_binding.py`
- Modify: `agent_orchestrator/infra/git/project_workspace.py:126-139`
- Test: `backend/tests/integration/test_repository_binding.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `GIT_NONINTERACTIVE_ENV: dict[str, str]` in `repository_binding.py`, imported by Tasks 3 and 4.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/integration/test_repository_binding.py`:

```python
def test_materialize_remote_does_not_block_on_a_credential_prompt(tmp_path):
    """A private https remote makes git ask for a username. There is no tty to
    answer it, so without GIT_TERMINAL_PROMPT=0 the worker blocks forever while
    holding a goal lease. It must fail fast instead."""
    from agent_orchestrator.domain.entities.project_definition import ProjectDefinition
    from agent_orchestrator.infra.git.project_workspace import ProjectWorkspaceResolver

    class _Projects:
        def get(self, project_id): raise NotImplementedError
        def list(self): return []

    resolver = ProjectWorkspaceResolver(_Projects(), tmp_path / "home")
    project = ProjectDefinition(
        id="p1",
        name="private",
        # RFC 5737 TEST-NET-1: never routable, so this cannot reach a real host.
        repo_url="https://192.0.2.1/private/repo.git",
    )
    destination = tmp_path / "clone"

    with pytest.raises(subprocess.CalledProcessError):
        resolver._materialize_remote(project, destination)
```

Add `import subprocess` and `import pytest` to the file's imports if absent.

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd backend && pytest tests/integration/test_repository_binding.py -p no:xdist -k credential_prompt -v --timeout=60`

Expected: FAIL — it hangs to the timeout, or raises something other than `CalledProcessError`. If your git build already declines the prompt, the test passes trivially; keep it anyway as the lock, and note that in the commit body.

- [ ] **Step 3: Add the environment guard**

In `agent_orchestrator/infra/git/repository_binding.py`, after the `_SCP_STYLE` definition:

```python
# git will happily block forever asking a human for a username. Nothing in the
# orchestrator runs on a tty — a worker holding a goal lease would simply stop —
# so every git subprocess that can reach a remote runs with prompting disabled
# and ssh in batch mode. Failing fast turns a hang into a classified error.
GIT_NONINTERACTIVE_ENV: dict[str, str] = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "echo",
    "SSH_ASKPASS": "echo",
    "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new",
}
```

- [ ] **Step 4: Apply it to the clone**

In `agent_orchestrator/infra/git/project_workspace.py`, add to the imports:

```python
from agent_orchestrator.infra.git.repository_binding import GIT_NONINTERACTIVE_ENV
```

and change the `subprocess.run` inside `_materialize_remote` to:

```python
        subprocess.run(
            ["git", "clone", "--", project.repo_url, str(destination)],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, **GIT_NONINTERACTIVE_ENV},
        )
```

Add `import os` to that file's imports.

- [ ] **Step 5: Run the test and the surrounding suites**

Run: `cd backend && pytest tests/integration/test_repository_binding.py tests/integration/test_git_workspace.py -v`

Expected: PASS, no regressions.

- [ ] **Step 6: Quality gates**

Run: `cd backend && ruff check agent_orchestrator tests --fix && mypy agent_orchestrator`

Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add backend/agent_orchestrator/infra/git/repository_binding.py \
        backend/agent_orchestrator/infra/git/project_workspace.py \
        backend/tests/integration/test_repository_binding.py
git commit -m "fix: a private remote could hang the worker instead of failing

_materialize_remote ran git clone under capture_output with no
GIT_TERMINAL_PROMPT=0. A private https remote makes git prompt for a
username, and there is no tty to answer it — so the worker blocked
indefinitely while holding a goal lease, with no error and no timeout.

Every git subprocess that can reach a remote now runs non-interactively."
```

---

## Task 2: Let the operator name the binding, and refuse a disagreement

**Files:**
- Modify: `agent_orchestrator/api/routers/reference.py:300-331`
- Test: `backend/tests/integration/test_reference_repos.py`

**Interfaces:**
- Consumes: `validate_repo_url(repo_url) -> RepositoryBinding` with `.kind` in `{"local","remote","scratch"}`, already in `infra/git/repository_binding.py`.
- Produces: `ProjectBody.binding: Literal["local","remote","scratch"] | None`, consumed by the Task 8 wizard.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/integration/test_reference_repos.py` (follow the file's existing client fixture; it is named `client` and is a `TestClient`):

```python
def test_naming_remote_with_no_url_is_refused(client):
    """The silent scratch substitution the phase forbids: an operator who says
    'remote' and leaves the URL blank must not quietly get a demo repository."""
    response = client.post(
        "/api/projects", json={"name": "p", "repo_url": None, "binding": "remote"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PROJECT_BINDING_INVALID"
    assert "remote" in response.json()["error"]["message"]


def test_naming_scratch_with_a_url_is_refused(client):
    response = client.post(
        "/api/projects",
        json={"name": "p", "repo_url": "https://example.com/a/b.git", "binding": "scratch"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PROJECT_BINDING_INVALID"


def test_a_binding_that_agrees_is_accepted(client):
    response = client.post(
        "/api/projects",
        json={"name": "p", "repo_url": "https://example.com/a/b.git", "binding": "remote"},
    )
    assert response.status_code == 201


def test_omitting_the_binding_still_works(client):
    """Every fixture and run-cycle.sh posts {name, repo_url}. Inference stays."""
    response = client.post("/api/projects", json={"name": "p", "repo_url": None})
    assert response.status_code == 201
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `cd backend && pytest tests/integration/test_reference_repos.py -k "binding or scratch or remote" -v`

Expected: the first two FAIL with 201 instead of 422; the last two PASS already.

- [ ] **Step 3: Implement the agreement check**

In `agent_orchestrator/api/routers/reference.py`, replace the `ProjectBody` class and add a helper above it:

```python
class ProjectBody(BaseModel):
    name: str
    repo_url: str | None = None
    # Optional on purpose: every fixture and run-cycle.sh posts {name, repo_url}
    # and inference stays the fallback. When present it is checked against what
    # the URL actually is, which is what stops "remote" with a blank URL from
    # silently becoming a scratch repository.
    binding: Literal["local", "remote", "scratch"] | None = None


def _checked_binding(body: ProjectBody) -> RepositoryBinding:
    binding = validate_repo_url(body.repo_url)
    if body.binding is not None and body.binding != binding.kind:
        raise ProjectBindingInvalidError(
            f"this project was created as '{body.binding}' but its repository URL "
            f"is {'empty' if not body.repo_url else repr(body.repo_url)}, "
            f"which is a '{binding.kind}' binding"
        )
    return binding
```

Then in both `create_project` and `update_project`, replace the bare
`validate_repo_url(body.repo_url)` call with `_checked_binding(body)`.

Add `Literal` to the `typing` import and `RepositoryBinding` to the existing
`repository_binding` import in that file.

- [ ] **Step 4: Run the tests**

Run: `cd backend && pytest tests/integration/test_reference_repos.py -v`

Expected: PASS.

- [ ] **Step 5: Prove no fixture regressed**

Run: `cd backend && pytest tests/integration/test_happy_path_fixture.py tests/integration/test_first_cycle_fixture.py tests/unit/test_fixture_docs_contract.py -v`

Expected: PASS — these post `{name, repo_url}` with no `binding`.

- [ ] **Step 6: Quality gates and commit**

```bash
cd backend && ruff check agent_orchestrator tests --fix && mypy agent_orchestrator
cd .. && git add backend/agent_orchestrator/api/routers/reference.py \
                 backend/tests/integration/test_reference_repos.py
git commit -m "feat(api): let a project name its binding, and refuse a disagreement

The UI's repository field is hinted 'Optional.', so nothing distinguishes
'I have no repository yet' from 'I left the box blank'. Naming the binding
makes the difference explicit and refusable at the API, not only in the UI.

Optional, so every fixture keeps working; inference stays the fallback."
```

---

## Task 3: Probe a remote without cloning it

**Files:**
- Modify: `agent_orchestrator/infra/git/repository_binding.py`
- Modify: `agent_orchestrator/api/routers/reference.py`
- Test: `backend/tests/integration/test_repository_binding.py`

**Interfaces:**
- Consumes: `GIT_NONINTERACTIVE_ENV` (Task 1).
- Produces: `probe_remote(repo_url: str, timeout_seconds: float = 5.0) -> RemoteProbe` and the frozen dataclass `RemoteProbe(reachable: bool, default_branch: str | None, problem: str | None, problem_kind: str | None)`, consumed by the Task 8 wizard.

**Why this is a separate endpoint and not part of `create`:** `repository_binding.py`'s module docstring records a deliberate decision against a network probe at write time — a create request must not block on a slow host. That reasoning holds for write-time validation and not for a wizard with a human watching, so the two are separated rather than the decision reversed. `create_project` must not call `probe_remote`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/integration/test_repository_binding.py`:

```python
def test_probe_classifies_an_unreachable_host():
    from agent_orchestrator.infra.git.repository_binding import probe_remote

    # RFC 5737 TEST-NET-1 — never routable.
    probe = probe_remote("https://192.0.2.1/a/b.git", timeout_seconds=2.0)

    assert probe.reachable is False
    assert probe.problem_kind in {"unreachable", "timeout", "needs_credentials"}
    assert probe.problem


def test_probe_reads_the_default_branch_of_a_local_repository(tmp_path):
    """A file:// URL is a remote as far as ls-remote is concerned, so the probe
    works offline and the test needs no network."""
    from agent_orchestrator.infra.git.repository_binding import probe_remote

    repo = tmp_path / "origin"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "trunk", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "--allow-empty", "-m", "seed"],
                   check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"})

    probe = probe_remote(f"file://{repo}")

    assert probe.reachable is True
    assert probe.default_branch == "trunk"
    assert probe.problem is None


def test_probe_route_reports_the_resolved_path_preview(client):
    response = client.post(
        "/api/projects/probe", json={"repo_url": "https://192.0.2.1/a/b.git"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["binding"] == "remote"
    assert body["reachable"] is False
    assert "/repos/" in body["resolved_path_preview"]
```

Add `import os` to the file's imports if absent.

- [ ] **Step 2: Run them and confirm they fail**

Run: `cd backend && pytest tests/integration/test_repository_binding.py -k probe -v`

Expected: FAIL — `ImportError: cannot import name 'probe_remote'`.

- [ ] **Step 3: Implement `probe_remote`**

Append to `agent_orchestrator/infra/git/repository_binding.py`:

```python
@dataclass(frozen=True)
class RemoteProbe:
    """What one `git ls-remote` says about a remote, classified.

    `problem_kind` exists because the wizard's next step differs per kind:
    needing credentials is a token problem, `not_found` is a typo, and the
    operator should not have to read git's stderr to tell them apart.
    """

    reachable: bool
    default_branch: str | None = None
    problem: str | None = None
    problem_kind: str | None = None  # needs_credentials | not_found | unreachable | timeout


def _classify_ls_remote_failure(stderr: str) -> str:
    lowered = stderr.lower()
    if "authentication" in lowered or "could not read username" in lowered or (
        "permission denied" in lowered
    ):
        return "needs_credentials"
    if "not found" in lowered or "does not exist" in lowered or "repository not found" in lowered:
        return "not_found"
    return "unreachable"


def probe_remote(repo_url: str, timeout_seconds: float = 5.0) -> RemoteProbe:
    """Ask a remote whether it exists, without downloading it.

    Never called from a write path — see the module docstring. This is the
    wizard's check, made at a moment a human is watching, and it must never
    block: non-interactive environment plus a hard timeout.
    """
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--symref", "--heads", "--", repo_url],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env={**os.environ, **GIT_NONINTERACTIVE_ENV},
        )
    except subprocess.TimeoutExpired:
        return RemoteProbe(
            reachable=False,
            problem=f"{repo_url} did not answer within {timeout_seconds:.0f}s",
            problem_kind="timeout",
        )

    if result.returncode != 0:
        stderr = result.stderr.strip()
        return RemoteProbe(
            reachable=False,
            problem=stderr or f"git ls-remote exited {result.returncode}",
            problem_kind=_classify_ls_remote_failure(stderr),
        )

    default_branch: str | None = None
    for line in result.stdout.splitlines():
        if line.startswith("ref:"):
            # "ref: refs/heads/trunk\tHEAD"
            default_branch = line.split()[1].removeprefix("refs/heads/")
            break
    return RemoteProbe(reachable=True, default_branch=default_branch)
```

Add `import os` to that module's imports.

- [ ] **Step 4: Add the route**

In `agent_orchestrator/api/routers/reference.py`, above the existing
`@router.get("/projects")`:

```python
class ProbeRequest(BaseModel):
    repo_url: str


class ProbeResponse(BaseModel):
    binding: str
    reachable: bool
    default_branch: str | None
    resolved_path_preview: str | None
    problem: str | None
    problem_kind: str | None


@router.post("/projects/probe", response_model=ProbeResponse)
def probe_project_repository(
    body: ProbeRequest, container: AppContainer = Depends(get_container)
) -> ProbeResponse:
    """Diagnose a repository URL before a project exists.

    Read-only: writes nothing and creates nothing. `create_project` does NOT
    call this — write-time validation stays network-free by design.
    """
    binding = validate_repo_url(body.repo_url)
    preview = container.workspace_resolver.repository_path_for(
        ProjectDefinition(id="<new>", name="<new>", repo_url=body.repo_url)
    )
    if binding.kind != "remote":
        return ProbeResponse(
            binding=binding.kind,
            reachable=True,
            default_branch=binding.default_branch,
            resolved_path_preview=str(preview),
            problem=None,
            problem_kind=None,
        )
    probe = probe_remote(body.repo_url)
    return ProbeResponse(
        binding=binding.kind,
        reachable=probe.reachable,
        default_branch=probe.default_branch,
        resolved_path_preview=str(preview),
        problem=probe.problem,
        problem_kind=probe.problem_kind,
    )
```

Add `probe_remote` to the `repository_binding` import.

- [ ] **Step 5: Run the tests**

Run: `cd backend && pytest tests/integration/test_repository_binding.py -v`

Expected: PASS.

- [ ] **Step 6: Add the matrix row**

In `docs/architecture/capability-matrix.md`, in the setup section, add a row for
`POST /api/projects/probe` — classified *implemented/exposed*, launch-critical,
consumer "Settings → project wizard (P8.1)".

Run: `cd backend && pytest tests/unit/test_capability_matrix.py -v`

Expected: PASS. This test fails the build in both directions, so a missing row is a hard error.

- [ ] **Step 7: Quality gates and commit**

```bash
cd backend && ruff check agent_orchestrator tests --fix && mypy agent_orchestrator
cd .. && git add backend/agent_orchestrator/infra/git/repository_binding.py \
                 backend/agent_orchestrator/api/routers/reference.py \
                 backend/tests/integration/test_repository_binding.py \
                 docs/architecture/capability-matrix.md
git commit -m "feat(api): probe a remote before binding a project to it

A remote is cloned lazily inside the worker, so a typo'd, private or
unreachable URL fails during a cycle rather than at setup — the failure
shape most likely to strand a first-time user.

The probe is its own endpoint rather than part of create, because
repository_binding.py records a deliberate decision against a network
check at write time. That reasoning holds for write-time validation and
not for a wizard with a human watching, so the two are separated rather
than the decision reversed."
```

---

## Task 4: Materialize a clone on request

**Files:**
- Modify: `agent_orchestrator/api/routers/reference.py`
- Test: `backend/tests/integration/test_reference_repos.py`

**Interfaces:**
- Consumes: `ProjectWorkspaceResolver.resolve(project_id)` and `.repository_path_for(project)`, both already public.
- Produces: `POST /api/projects/{project_id}/clone` → `{resolved_path, default_branch, already_present}`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/integration/test_reference_repos.py`:

```python
def test_clone_materializes_a_remote_and_is_idempotent(client, tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
    subprocess.run(["git", "init", "-b", "main", str(origin)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(origin), "commit", "--allow-empty", "-m", "seed"],
                   check=True, capture_output=True, env=env)

    created = client.post(
        "/api/projects",
        json={"name": "p", "repo_url": f"file://{origin}", "binding": "local"},
    )
    # file:// resolves to the path itself, so this project is 'local' and the
    # clone endpoint reports it already present rather than copying anything.
    project_id = created.json()["id"]

    first = client.post(f"/api/projects/{project_id}/clone")
    assert first.status_code == 200
    assert first.json()["already_present"] is True

    second = client.post(f"/api/projects/{project_id}/clone")
    assert second.status_code == 200
    assert second.json()["resolved_path"] == first.json()["resolved_path"]
```

Add `import os` and `import subprocess` to the file's imports if absent.

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd backend && pytest tests/integration/test_reference_repos.py -k clone -v`

Expected: FAIL with 404 — no such route.

- [ ] **Step 3: Add the route**

In `agent_orchestrator/api/routers/reference.py`, after `project_readiness`:

```python
class CloneResponse(BaseModel):
    resolved_path: str
    default_branch: str | None
    already_present: bool


@router.post("/projects/{project_id}/clone", response_model=CloneResponse)
def clone_project_repository(
    project_id: str, container: AppContainer = Depends(get_container)
) -> CloneResponse:
    """Materialize this project's repository now, rather than during its first
    cycle. Idempotent: an existing clone is reported, never re-fetched."""
    project = container.project_repo.get(project_id)
    destination = container.workspace_resolver.repository_path_for(project)
    already_present = (destination / ".git").exists()
    workspace = container.workspace_resolver.resolve(project_id)
    return CloneResponse(
        resolved_path=str(destination),
        default_branch=workspace.default_branch,
        already_present=already_present,
    )
```

If `GitBranchWorkspace` does not expose `default_branch` as a public attribute,
use `default_branch_of(destination)` from `repository_binding` instead and add it
to the import — do not add a new attribute to the workspace class.

- [ ] **Step 4: Run the test**

Run: `cd backend && pytest tests/integration/test_reference_repos.py -k clone -v`

Expected: PASS.

- [ ] **Step 5: Matrix row, quality gates, commit**

Add `POST /api/projects/{project_id}/clone` to `docs/architecture/capability-matrix.md`.

```bash
cd backend && pytest tests/unit/test_capability_matrix.py -v \
  && ruff check agent_orchestrator tests --fix && mypy agent_orchestrator
cd .. && git add backend/agent_orchestrator/api/routers/reference.py \
                 backend/tests/integration/test_reference_repos.py \
                 docs/architecture/capability-matrix.md
git commit -m "feat(api): materialize a project's clone on request

So an operator pays the clone cost at setup, with the result visible,
instead of inside their first cycle. Idempotent."
```

---

## Task 5: The forge port, its fallback, and its fake

No network, no GitHub. This task defines the seam every later task plugs into.

**Files:**
- Create: `agent_orchestrator/app/forge_port.py`
- Create: `agent_orchestrator/infra/forge/__init__.py`
- Create: `agent_orchestrator/infra/forge/no_forge.py`
- Modify: `agent_orchestrator/app/testing/fakes.py`
- Modify: `agent_orchestrator/infra/errors.py`
- Modify: `agent_orchestrator/api/exceptions.py`
- Test: `backend/tests/unit/test_forge_port.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces — every later task depends on these exact names:
  - `PullRequestRef(url: str, number: int)` — frozen dataclass
  - `ForgePort` protocol: `push_branch(self, repo: Path, branch: str) -> None`, `open_pull_request(self, *, head: str, base: str, title: str, body: str) -> PullRequestRef`
  - `ForgeNotConfiguredError` (code `FORGE_NOT_CONFIGURED`)
  - `NoForge()` — raises `ForgeNotConfiguredError` from both methods
  - `FakeForge(fail_on: str | None = None)` with `.pushes: list[tuple[Path, str]]` and `.pull_requests: list[dict[str, str]]`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_forge_port.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from agent_orchestrator.app.forge_port import ForgeNotConfiguredError, ForgePort, PullRequestRef
from agent_orchestrator.app.testing.fakes import FakeForge
from agent_orchestrator.infra.forge.no_forge import NoForge


def test_no_forge_refuses_both_operations():
    """NoForge is the permanent fallback, not a placeholder: an installation
    with no GitHub token must keep working and say so, exactly as NoSandbox
    reports sandbox=disabled rather than refusing to run."""
    forge = NoForge()

    with pytest.raises(ForgeNotConfiguredError):
        forge.push_branch(Path("/tmp/repo"), "cycle/abc")
    with pytest.raises(ForgeNotConfiguredError):
        forge.open_pull_request(head="cycle/abc", base="main", title="t", body="b")


def test_both_adapters_satisfy_the_protocol():
    assert isinstance(NoForge(), ForgePort)
    assert isinstance(FakeForge(), ForgePort)


def test_fake_forge_records_what_it_was_asked_to_do():
    forge = FakeForge()

    forge.push_branch(Path("/tmp/repo"), "cycle/abc")
    ref = forge.open_pull_request(head="cycle/abc", base="main", title="t", body="b")

    assert forge.pushes == [(Path("/tmp/repo"), "cycle/abc")]
    assert forge.pull_requests == [
        {"head": "cycle/abc", "base": "main", "title": "t", "body": "b"}
    ]
    assert isinstance(ref, PullRequestRef)
    assert ref.number == 1


def test_fake_forge_can_be_scripted_to_fail_at_either_step():
    from agent_orchestrator.app.forge_port import ForgeRequestFailedError

    with pytest.raises(ForgeRequestFailedError):
        FakeForge(fail_on="push").push_branch(Path("/tmp/repo"), "cycle/abc")

    pushed = FakeForge(fail_on="pull_request")
    pushed.push_branch(Path("/tmp/repo"), "cycle/abc")
    with pytest.raises(ForgeRequestFailedError):
        pushed.open_pull_request(head="cycle/abc", base="main", title="t", body="b")
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd backend && pytest tests/unit/test_forge_port.py -v`

Expected: FAIL — `ModuleNotFoundError: agent_orchestrator.app.forge_port`.

- [ ] **Step 3: Write the port**

Create `agent_orchestrator/app/forge_port.py`:

```python
"""The Forge port: pushing a verified cycle branch to a hosting service and
opening a pull request for it.

Deliberately NOT a domain concept — the frozen domain never sees these types —
and deliberately not part of the Workspace port, whose contract is local git
only. An authenticated push spends the forge's credential, not the workspace's,
so the two operations that need that credential live together here.

Adapters live in infra: `NoForge` (agent_orchestrator/infra/forge/no_forge.py)
is the PERMANENT fallback, not a placeholder — an installation with no token
must keep running and record a disposition the operator typed, exactly as it
does today. `GitHubForge` plugs in beside it without any caller changing.

Hard scope limits, enforced by having no method for either: this port opens a
pull request and cannot merge one, and it pushes one named branch and cannot
touch a default branch.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from agent_orchestrator.domain.errors.base import BaseAppException


class ForgeError(BaseAppException):
    """Base for every forge failure. Subclasses carry the stable code."""

    code = "FORGE_REQUEST_FAILED"


class ForgeNotConfiguredError(ForgeError):
    """Asked to really open a pull request with no forge bound to the project."""

    code = "FORGE_NOT_CONFIGURED"


class ForgeAuthFailedError(ForgeError):
    """The token was rejected, or cannot push to the repository."""

    code = "FORGE_AUTH_FAILED"


class ForgeRepoNotFoundError(ForgeError):
    """`owner/repo` does not resolve for this token."""

    code = "FORGE_REPO_NOT_FOUND"


class ForgePushFailedError(ForgeError):
    """The push reached the remote and was refused."""

    code = "FORGE_PUSH_FAILED"


class ForgeRequestFailedError(ForgeError):
    """The forge API failed or was unreachable."""

    code = "FORGE_REQUEST_FAILED"


@dataclass(frozen=True)
class PullRequestRef:
    url: str
    number: int


@runtime_checkable
class ForgePort(Protocol):
    def push_branch(self, repo: Path, branch: str) -> None:
        """Push exactly `branch`. Never the default branch, never a force push."""
        ...

    def open_pull_request(
        self, *, head: str, base: str, title: str, body: str
    ) -> PullRequestRef:
        """Open a pull request. There is deliberately no merge counterpart."""
        ...
```

- [ ] **Step 4: Write `NoForge`**

Create `agent_orchestrator/infra/forge/__init__.py` (empty) and
`agent_orchestrator/infra/forge/no_forge.py`:

```python
"""The permanent no-forge fallback. See agent_orchestrator/app/forge_port.py."""

from __future__ import annotations

from pathlib import Path

from agent_orchestrator.app.forge_port import ForgeNotConfiguredError, PullRequestRef

_MESSAGE = (
    "No forge is bound to this project, so the orchestrator cannot open a pull "
    "request for you. Bind one under Settings → Projects → delivery, or record "
    "the disposition yourself with the reference you used."
)


class NoForge:
    def push_branch(self, repo: Path, branch: str) -> None:
        raise ForgeNotConfiguredError(_MESSAGE)

    def open_pull_request(
        self, *, head: str, base: str, title: str, body: str
    ) -> PullRequestRef:
        raise ForgeNotConfiguredError(_MESSAGE)
```

- [ ] **Step 5: Write `FakeForge`**

Append to `agent_orchestrator/app/testing/fakes.py`:

```python
class FakeForge:
    """In-memory ForgePort. `fail_on` scripts a failure at either step so a
    test can prove the publication gate stays open and nothing is recorded."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.pushes: list[tuple[Path, str]] = []
        self.pull_requests: list[dict[str, str]] = []
        self._fail_on = fail_on

    def push_branch(self, repo: Path, branch: str) -> None:
        if self._fail_on == "push":
            raise ForgeRequestFailedError("scripted push failure")
        self.pushes.append((repo, branch))

    def open_pull_request(
        self, *, head: str, base: str, title: str, body: str
    ) -> PullRequestRef:
        if self._fail_on == "pull_request":
            raise ForgeRequestFailedError("scripted pull-request failure")
        self.pull_requests.append(
            {"head": head, "base": base, "title": title, "body": body}
        )
        number = len(self.pull_requests)
        return PullRequestRef(
            url=f"https://github.test/o/r/pull/{number}", number=number
        )
```

Add to that file's imports:

```python
from pathlib import Path

from agent_orchestrator.app.forge_port import (
    ForgeRequestFailedError,
    PullRequestRef,
)
```

- [ ] **Step 6: Register the error codes**

In `agent_orchestrator/api/exceptions.py`, add to `_STATUS_BY_CODE` in the 422 block:

```python
    "FORGE_NOT_CONFIGURED": 422,
    "FORGE_AUTH_FAILED": 422,
    "FORGE_REPO_NOT_FOUND": 422,
```

and in the 502 block:

```python
    "FORGE_PUSH_FAILED": 502,
    "FORGE_REQUEST_FAILED": 502,
```

`ForgeError` subclasses `BaseAppException`, which the existing handler already
maps by `.code`. Confirm by reading the handler registrations at the bottom of
`exceptions.py`; if it registers `DomainError` and `InfrastructureError`
separately, add a registration for `ForgeError` alongside them using the same
`_envelope(...)` shape — do not add a router-level `try/except`.

- [ ] **Step 7: Run the tests**

Run: `cd backend && pytest tests/unit/test_forge_port.py -v && ruff check agent_orchestrator tests --fix && mypy agent_orchestrator`

Expected: PASS, clean.

- [ ] **Step 8: Commit**

```bash
git add backend/agent_orchestrator/app/forge_port.py \
        backend/agent_orchestrator/infra/forge/ \
        backend/agent_orchestrator/app/testing/fakes.py \
        backend/agent_orchestrator/api/exceptions.py \
        backend/tests/unit/test_forge_port.py
git commit -m "feat(app): the Forge port, its permanent fallback, and its fake

Beside sandbox_port.py and on the same principle: infrastructure-facing,
never seen by the frozen domain, with a NoForge fallback that is permanent
rather than a placeholder.

The scope limits are enforced structurally — there is no merge method and
no way to name a branch other than the one passed in."
```

---

## Task 6: The GitHub adapter and the project's forge binding

**Files:**
- Create: `agent_orchestrator/infra/forge/github.py`
- Create: `agent_orchestrator/infra/forge/binding.py`
- Modify: `agent_orchestrator/infra/db/secret_ref.py`
- Modify: `agent_orchestrator/infra/container.py`
- Modify: `agent_orchestrator/api/routers/reference.py`
- Modify: `pyproject.toml`
- Test: `backend/tests/integration/test_github_forge.py` (create)

**Interfaces:**
- Consumes: `ForgePort`, `PullRequestRef`, and the five error classes (Task 5); `SqliteSecretStore.put/resolve/delete`; `SqliteConfigStore.get/set/delete`.
- Produces:
  - `SecretRef.for_forge(project_id: str) -> SecretRef`
  - `ForgeBinding(provider: str, repository: str, token_ref: str)` and `read_binding(config_store, project_id) -> ForgeBinding | None`, `write_binding(...)`, `clear_binding(...)` in `infra/forge/binding.py`
  - `GitHubForge(repository: str, token: SecretStr)` and `verify_github_token(repository: str, token: SecretStr) -> GitHubIdentity`
  - `AppContainer.forge_for(project_id: str) -> ForgePort`

- [ ] **Step 1: Declare the dependency**

In `backend/pyproject.toml`, add `"httpx>=0.28"` to `[project] dependencies`.
It currently reaches runtime only transitively through `openai`, which is not a
contract.

Run: `cd backend && uv pip install -e .[dev]`

- [ ] **Step 2: Write the failing test**

Create `backend/tests/integration/test_github_forge.py`:

```python
from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr

from agent_orchestrator.app.forge_port import (
    ForgeAuthFailedError,
    ForgeRepoNotFoundError,
)
from agent_orchestrator.infra.forge.github import GitHubForge, verify_github_token


def _transport(handler):
    return httpx.MockTransport(handler)


def test_verify_accepts_a_token_that_can_push():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/acme/widgets"
        assert request.headers["authorization"] == "Bearer ghp_test"
        return httpx.Response(
            200,
            json={
                "full_name": "acme/widgets",
                "default_branch": "main",
                "permissions": {"push": True},
            },
        )

    identity = verify_github_token(
        "acme/widgets", SecretStr("ghp_test"), transport=_transport(handler)
    )

    assert identity.repository == "acme/widgets"
    assert identity.default_branch == "main"


def test_verify_refuses_a_token_that_cannot_push():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "full_name": "acme/widgets",
                "default_branch": "main",
                "permissions": {"push": False},
            },
        )

    with pytest.raises(ForgeAuthFailedError) as exc:
        verify_github_token(
            "acme/widgets", SecretStr("ghp_test"), transport=_transport(handler)
        )
    assert "push" in str(exc.value).lower()


def test_verify_maps_404_to_repo_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    with pytest.raises(ForgeRepoNotFoundError):
        verify_github_token(
            "acme/widgets", SecretStr("ghp_test"), transport=_transport(handler)
        )


def test_verify_maps_401_to_auth_failed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    with pytest.raises(ForgeAuthFailedError):
        verify_github_token(
            "acme/widgets", SecretStr("ghp_test"), transport=_transport(handler)
        )


def test_open_pull_request_returns_the_real_url():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/repos/acme/widgets/pulls"
        return httpx.Response(
            201, json={"html_url": "https://github.com/acme/widgets/pull/7", "number": 7}
        )

    forge = GitHubForge(
        "acme/widgets", SecretStr("ghp_test"), transport=_transport(handler)
    )
    ref = forge.open_pull_request(
        head="cycle/abc", base="main", title="t", body="b"
    )

    assert ref.url == "https://github.com/acme/widgets/pull/7"
    assert ref.number == 7


def test_the_token_never_appears_in_the_repr():
    forge = GitHubForge("acme/widgets", SecretStr("ghp_supersecret"))
    assert "ghp_supersecret" not in repr(forge)
```

- [ ] **Step 3: Run it and confirm it fails**

Run: `cd backend && pytest tests/integration/test_github_forge.py -v`

Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 4: Write `GitHubForge`**

Create `agent_orchestrator/infra/forge/github.py`:

```python
"""GitHub adapter for the Forge port.

GitHub only, on purpose: guessing GitLab or Gitea semantics with no user asking
is the completeness the roadmap's scope discipline forbids. The port makes a
second adapter cheap when someone does ask.

The token is held as a SecretStr and crosses into plaintext only inside the
Authorization header of a single request. It never reaches a log line, a repr,
or an exception message.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import httpx
import structlog
from pydantic import SecretStr

from agent_orchestrator.app.forge_port import (
    ForgeAuthFailedError,
    ForgePushFailedError,
    ForgeRepoNotFoundError,
    ForgeRequestFailedError,
    PullRequestRef,
)
from agent_orchestrator.infra.git.repository_binding import GIT_NONINTERACTIVE_ENV

log = structlog.get_logger(__name__)

_API = "https://api.github.com"
_TIMEOUT = 15.0


@dataclass(frozen=True)
class GitHubIdentity:
    repository: str
    default_branch: str


def _headers(token: SecretStr) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token.get_secret_value()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _raise_for_status(response: httpx.Response, repository: str) -> None:
    if response.status_code in (401, 403):
        raise ForgeAuthFailedError(
            f"GitHub rejected the token for {repository} "
            f"({response.status_code}). Check that it is valid and has "
            "repository write access."
        )
    if response.status_code == 404:
        raise ForgeRepoNotFoundError(
            f"GitHub has no repository {repository} reachable with this token. "
            "A private repository the token cannot see also reports 404."
        )
    if response.status_code >= 400:
        raise ForgeRequestFailedError(
            f"GitHub returned {response.status_code} for {repository}"
        )


def verify_github_token(
    repository: str,
    token: SecretStr,
    *,
    transport: httpx.BaseTransport | None = None,
) -> GitHubIdentity:
    """One call that answers three questions at once: does the repository
    exist, does the token reach it, and can it push. Called at save time so a
    bad token fails at setup rather than at the publication gate."""
    try:
        with httpx.Client(base_url=_API, timeout=_TIMEOUT, transport=transport) as client:
            response = client.get(f"/repos/{repository}", headers=_headers(token))
    except httpx.HTTPError as exc:
        raise ForgeRequestFailedError(f"could not reach GitHub: {exc}") from exc

    _raise_for_status(response, repository)
    payload = response.json()
    if not payload.get("permissions", {}).get("push", False):
        raise ForgeAuthFailedError(
            f"the token reaches {repository} but cannot push to it; "
            "a pull request needs write access"
        )
    return GitHubIdentity(
        repository=payload["full_name"], default_branch=payload["default_branch"]
    )


class GitHubForge:
    def __init__(
        self,
        repository: str,
        token: SecretStr,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._repository = repository
        self._token = token
        self._transport = transport

    def __repr__(self) -> str:
        # Explicit, because the default dataclass-ish repr of a SecretStr is
        # safe but this class is not a dataclass and a future field might not be.
        return f"GitHubForge(repository={self._repository!r})"

    def push_branch(self, repo: Path, branch: str) -> None:
        url = (
            f"https://x-access-token:{self._token.get_secret_value()}"
            f"@github.com/{self._repository}.git"
        )
        result = subprocess.run(
            ["git", "-C", str(repo), "push", url, f"refs/heads/{branch}:refs/heads/{branch}"],
            capture_output=True,
            text=True,
            env={**GIT_NONINTERACTIVE_ENV},
        )
        if result.returncode != 0:
            # stderr can echo the remote URL, which carries the token.
            scrubbed = result.stderr.replace(self._token.get_secret_value(), "***")
            log.warning("forge.push_failed", repository=self._repository, branch=branch)
            raise ForgePushFailedError(
                f"pushing {branch} to {self._repository} was refused: {scrubbed.strip()}"
            )
        log.info("forge.pushed", repository=self._repository, branch=branch)

    def open_pull_request(
        self, *, head: str, base: str, title: str, body: str
    ) -> PullRequestRef:
        try:
            with httpx.Client(
                base_url=_API, timeout=_TIMEOUT, transport=self._transport
            ) as client:
                response = client.post(
                    f"/repos/{self._repository}/pulls",
                    headers=_headers(self._token),
                    json={"head": head, "base": base, "title": title, "body": body},
                )
        except httpx.HTTPError as exc:
            raise ForgeRequestFailedError(f"could not reach GitHub: {exc}") from exc

        _raise_for_status(response, self._repository)
        payload = response.json()
        log.info(
            "forge.pull_request_opened",
            repository=self._repository,
            number=payload["number"],
        )
        return PullRequestRef(url=payload["html_url"], number=payload["number"])
```

Note `env={**GIT_NONINTERACTIVE_ENV}` on the push deliberately does NOT inherit
`os.environ`: a push carrying its own credential in the URL must not also pick
up an ambient credential helper that could redirect it.

- [ ] **Step 5: Write the binding module**

Create `agent_orchestrator/infra/forge/binding.py`:

```python
"""Where a project's forge binding is stored.

Deliberately NOT fields on ProjectDefinition: that is a frozen-domain entity,
and adding to it would need a decision-log entry and an un-freeze. The config
store is already two-tier with a project id as a scope, so the binding fits
with no domain change at all.

This module is the ONE place the three key names live.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_orchestrator.infra.db.secret_ref import SecretRef

PROVIDER_KEY = "forge.provider"
REPOSITORY_KEY = "forge.repository"
TOKEN_REF_KEY = "forge.token_ref"


@dataclass(frozen=True)
class ForgeBinding:
    provider: str
    repository: str
    token_ref: str


def read_binding(config_store, project_id: str) -> ForgeBinding | None:
    provider = config_store.get(project_id, PROVIDER_KEY)
    repository = config_store.get(project_id, REPOSITORY_KEY)
    token_ref = config_store.get(project_id, TOKEN_REF_KEY)
    if not provider or not repository or not token_ref:
        return None
    return ForgeBinding(provider=provider, repository=repository, token_ref=token_ref)


def write_binding(config_store, project_id: str, repository: str) -> ForgeBinding:
    token_ref = SecretRef.for_forge(project_id).uri
    config_store.set(project_id, PROVIDER_KEY, "github")
    config_store.set(project_id, REPOSITORY_KEY, repository)
    config_store.set(project_id, TOKEN_REF_KEY, token_ref)
    return ForgeBinding(provider="github", repository=repository, token_ref=token_ref)


def clear_binding(config_store, project_id: str) -> None:
    for key in (PROVIDER_KEY, REPOSITORY_KEY, TOKEN_REF_KEY):
        config_store.delete(project_id, key)
```

Check `SqliteConfigStore`'s actual method names in
`agent_orchestrator/infra/db/reference_repos.py:546` and match them exactly; if
there is no `delete`, use whatever the class provides (e.g. `unset`) rather than
adding a method.

- [ ] **Step 6: Add the secret ref and the container factory**

In `agent_orchestrator/infra/db/secret_ref.py`, beside `for_provider`:

```python
    @classmethod
    def for_forge(cls, project_id: str) -> "SecretRef":
        """Canonical ref for one project's forge token. Per project, not
        global: two projects can live on different accounts, and one credential
        spanning every project is the wrong blast radius for a local tool."""
        return cls(uri=f"secret://forge/{project_id}")
```

In `agent_orchestrator/infra/container.py`, add a method (not a
`cached_property` — it takes an argument, and the binding can change between
calls):

```python
    def forge_for(self, project_id: str) -> ForgePort:
        """The forge bound to this project, or the permanent no-forge fallback.

        Re-read per call on purpose, for the same reason `reasoner` became a
        LiveReasoner: a binding written in Settings must land on the next
        publication, not the next worker restart.
        """
        binding = read_binding(self.config_store, project_id)
        if binding is None:
            return NoForge()
        token = self.secret_store.resolve(SecretRef(uri=binding.token_ref))
        return GitHubForge(binding.repository, token)
```

with the corresponding imports.

- [ ] **Step 7: Add the three binding routes**

In `agent_orchestrator/api/routers/reference.py`:

```python
class ForgeBindingBody(BaseModel):
    repository: str  # "owner/repo"
    token: str


class ForgeBindingResponse(BaseModel):
    provider: str
    repository: str
    default_branch: str
    # Never the token, and never a prefix of it.


@router.get("/projects/{project_id}/forge")
def get_forge_binding(
    project_id: str, container: AppContainer = Depends(get_container)
) -> ForgeBindingResponse | None:
    binding = read_binding(container.config_store, project_id)
    if binding is None:
        return None
    return ForgeBindingResponse(
        provider=binding.provider, repository=binding.repository, default_branch=""
    )


@router.put("/projects/{project_id}/forge", response_model=ForgeBindingResponse)
def set_forge_binding(
    project_id: str,
    body: ForgeBindingBody,
    container: AppContainer = Depends(get_container),
) -> ForgeBindingResponse:
    """Verify the token against the exact repository BEFORE storing anything,
    so a credential that cannot push fails at setup rather than at the
    publication gate twenty-five minutes into a cycle."""
    container.project_repo.get(project_id)  # 404s on an unknown project
    identity = verify_github_token(body.repository, SecretStr(body.token))
    container.secret_store.put(SecretRef.for_forge(project_id), body.token)
    binding = write_binding(container.config_store, project_id, identity.repository)
    return ForgeBindingResponse(
        provider=binding.provider,
        repository=binding.repository,
        default_branch=identity.default_branch,
    )


@router.delete("/projects/{project_id}/forge", status_code=204)
def delete_forge_binding(
    project_id: str, container: AppContainer = Depends(get_container)
) -> None:
    clear_binding(container.config_store, project_id)
    container.secret_store.delete(SecretRef.for_forge(project_id))
```

If `SqliteSecretStore` has no `delete`, check the class and use its actual
removal method; do not add one.

- [ ] **Step 8: Run everything and commit**

```bash
cd backend && pytest tests/integration/test_github_forge.py tests/integration/test_secret_store.py -v \
  && pytest tests/unit/test_capability_matrix.py -v \
  && ruff check agent_orchestrator tests --fix && mypy agent_orchestrator
```

Add the three `/projects/{project_id}/forge` rows to
`docs/architecture/capability-matrix.md` before the matrix test will pass.

```bash
git add backend/agent_orchestrator/infra/forge/ \
        backend/agent_orchestrator/infra/db/secret_ref.py \
        backend/agent_orchestrator/infra/container.py \
        backend/agent_orchestrator/api/routers/reference.py \
        backend/pyproject.toml \
        backend/tests/integration/test_github_forge.py \
        docs/architecture/capability-matrix.md
git commit -m "feat(infra): the GitHub adapter and a per-project forge binding

The token is verified against the exact repository at save time — one call
that answers whether the repo exists, whether the token reaches it, and
whether it can push — so a bad credential fails at setup.

The binding lives in the project-scoped config store and the token in the
existing secret store, so ProjectDefinition is untouched and no domain
un-freeze is needed."
```

---

## Task 7: Publication that really opens the pull request

**Files:**
- Create: `agent_orchestrator/app/pr_body.py`
- Create: `agent_orchestrator/app/use_cases/publish_cycle.py`
- Modify: `agent_orchestrator/api/routers/plans.py:1194-1209`
- Test: `backend/tests/integration/test_publish_cycle.py` (create)

**Interfaces:**
- Consumes: `ForgePort`, `PullRequestRef`, `ForgeError` subclasses (Task 5); `AppContainer.forge_for` (Task 6); `record_output_disposition(plan_id, gate_id, revision, disposition, output_reference, uow, clock)` (existing, `app/use_cases/cyclic_planning.py:325`); `branch_names.cycle_branch(cycle_id)` (existing, `app/branch_names.py`).
- Produces: `publish_cycle(plan_id, gate_id, revision, disposition, output_reference, uow_factory, clock, forge, repo_path, default_branch) -> str | None` returning the recorded `output_reference`.

**The ordering is the point of this task.** The push and the API call must happen *outside* any transaction (invariant #5), and the disposition must be recorded *only after* the PR exists — so a forge failure leaves the gate open with nothing half-written.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/integration/test_publish_cycle.py`. Build the plan with the
existing helper the cyclic integration tests use — read
`backend/tests/integration/cyclic_walk.py` and reuse it rather than constructing
a plan by hand.

```python
from __future__ import annotations

import pytest

from agent_orchestrator.app.forge_port import ForgeRequestFailedError
from agent_orchestrator.app.testing.fakes import FakeForge
from agent_orchestrator.app.use_cases.publish_cycle import publish_cycle
from agent_orchestrator.domain.entities.planning_artifacts import OutputDisposition


def test_open_pr_pushes_then_records_the_real_url(published_cycle):
    """The recorded reference is a fact the orchestrator produced, not text a
    human typed — the whole point of promoting forge publication into P8.1."""
    env, plan_id, gate_id, cycle_id = published_cycle
    forge = FakeForge()

    reference = publish_cycle(
        plan_id=plan_id,
        gate_id=gate_id,
        revision=1,
        disposition=OutputDisposition.OPEN_PR,
        output_reference=None,
        uow_factory=env.new_unit_of_work,
        clock=env.clock,
        forge=forge,
        repo_path=env.repo_path,
        default_branch="main",
    )

    assert forge.pushes == [(env.repo_path, f"cycle/{cycle_id}")]
    assert reference == "https://github.test/o/r/pull/1"

    plan = env.new_unit_of_work().plans.get(plan_id)
    cycle = next(c for c in plan.cycles if c.id == cycle_id)
    assert cycle.output_reference == "https://github.test/o/r/pull/1"


def test_the_push_happens_before_the_pull_request(published_cycle):
    env, plan_id, gate_id, cycle_id = published_cycle
    forge = FakeForge(fail_on="pull_request")

    with pytest.raises(ForgeRequestFailedError):
        publish_cycle(
            plan_id=plan_id, gate_id=gate_id, revision=1,
            disposition=OutputDisposition.OPEN_PR, output_reference=None,
            uow_factory=env.new_unit_of_work, clock=env.clock, forge=forge,
            repo_path=env.repo_path, default_branch="main",
        )

    assert forge.pushes  # the push did happen
    assert forge.pull_requests == []


def test_a_forge_failure_leaves_the_gate_open_and_records_nothing(published_cycle):
    """The invariant that makes this safe: the disposition is written only
    after the pull request exists."""
    env, plan_id, gate_id, cycle_id = published_cycle
    forge = FakeForge(fail_on="push")

    with pytest.raises(ForgeRequestFailedError):
        publish_cycle(
            plan_id=plan_id, gate_id=gate_id, revision=1,
            disposition=OutputDisposition.OPEN_PR, output_reference=None,
            uow_factory=env.new_unit_of_work, clock=env.clock, forge=forge,
            repo_path=env.repo_path, default_branch="main",
        )

    plan = env.new_unit_of_work().plans.get(plan_id)
    cycle = next(c for c in plan.cycles if c.id == cycle_id)
    assert cycle.output_disposition is None
    assert plan.open_gate is not None


def test_retain_branch_never_touches_the_forge(published_cycle):
    env, plan_id, gate_id, cycle_id = published_cycle
    forge = FakeForge(fail_on="push")  # would raise if consulted

    publish_cycle(
        plan_id=plan_id, gate_id=gate_id, revision=1,
        disposition=OutputDisposition.RETAIN_BRANCH,
        output_reference=f"cycle/{cycle_id}",
        uow_factory=env.new_unit_of_work, clock=env.clock, forge=forge,
        repo_path=env.repo_path, default_branch="main",
    )

    assert forge.pushes == []


def test_open_pr_with_no_forge_still_records_a_typed_reference(published_cycle):
    """Existing behaviour is not removed: an installation with no token keeps
    recording the disposition the operator typed."""
    from agent_orchestrator.infra.forge.no_forge import NoForge

    env, plan_id, gate_id, cycle_id = published_cycle

    reference = publish_cycle(
        plan_id=plan_id, gate_id=gate_id, revision=1,
        disposition=OutputDisposition.OPEN_PR,
        output_reference="https://github.com/me/mine/pull/4",
        uow_factory=env.new_unit_of_work, clock=env.clock, forge=NoForge(),
        repo_path=env.repo_path, default_branch="main",
    )

    assert reference == "https://github.com/me/mine/pull/4"
```

Write a `published_cycle` fixture in the same file that drives a cyclic plan to
an open completion gate using `cyclic_walk.py`, and yields
`(env, plan_id, gate_id, cycle_id)` where `env.repo_path` is the project's
repository path. Read `test_cycle_evidence_api.py` for the closest existing
example of reaching that state.

- [ ] **Step 2: Run them and confirm they fail**

Run: `cd backend && pytest tests/integration/test_publish_cycle.py -v`

Expected: FAIL — `ModuleNotFoundError: ...use_cases.publish_cycle`.

- [ ] **Step 3: Write the PR body renderer**

Create `agent_orchestrator/app/pr_body.py`:

```python
"""Render a cycle's accepted evidence into a pull-request body.

Pure: takes data, returns a string, touches nothing. The evidence is the
product's actual argument — a reviewer who can see that this exact command
exited 0 against this candidate commit, and that the test was authored in a
separate commit that was RED first, reviews differently from one handed an
anonymous diff — so it belongs in the place a reviewer will read.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvidenceLine:
    task_title: str
    command: str
    exit_code: int
    candidate_sha: str


def render_pr_title(cycle_intent: str) -> str:
    first_line = cycle_intent.strip().splitlines()[0] if cycle_intent.strip() else "cycle"
    return first_line[:72]


def render_pr_body(
    *,
    cycle_id: str,
    cycle_intent: str,
    evidence: list[EvidenceLine],
    protected_paths: list[str],
) -> str:
    lines = [
        cycle_intent.strip(),
        "",
        "## Verification evidence",
        "",
        "Every task below reached DONE with revision-bound evidence; nothing was",
        "promoted without it.",
        "",
        "| Task | Command | Exit | Candidate |",
        "|---|---|---|---|",
    ]
    for item in evidence:
        lines.append(
            f"| {item.task_title} | `{item.command}` | {item.exit_code} "
            f"| `{item.candidate_sha[:8]}` |"
        )
    if protected_paths:
        lines += [
            "",
            "## Protected scope",
            "",
            "Paths the agent was not permitted to modify:",
            "",
            *(f"- `{path}`" for path in protected_paths),
        ]
    lines += [
        "",
        "---",
        "",
        f"Opened by the agent orchestrator for cycle `{cycle_id}`. "
        "The orchestrator does not merge; that decision is yours.",
    ]
    return "\n".join(lines)
```

- [ ] **Step 4: Write the use case**

Create `agent_orchestrator/app/use_cases/publish_cycle.py`:

```python
"""Publication: the one place a side effect precedes a recorded disposition.

record_output_disposition does everything inside one transaction, which is
correct when the disposition is a claim a human typed. When the orchestrator
opens the pull request itself, the push and the API call are side effects, and
architectural invariant #5 says those never run inside a transaction.

So the order here is deliberate and load-bearing:

  1. read-only pre-check, no transaction  — cheap refusal before anything external
  2. push, then open the PR, outside      — the side effects
  3. record the disposition               — only now, with the real URL

A failure at step 2 leaves the gate open and nothing written. The operator can
retry, or pick retain_branch instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import structlog

from agent_orchestrator.app.branch_names import cycle_branch
from agent_orchestrator.app.forge_port import ForgeNotConfiguredError, ForgePort
from agent_orchestrator.app.ports import UnitOfWork
from agent_orchestrator.app.pr_body import EvidenceLine, render_pr_body, render_pr_title
from agent_orchestrator.app.use_cases.cyclic_planning import record_output_disposition
from agent_orchestrator.domain.entities.planning_artifacts import OutputDisposition
from agent_orchestrator.domain.ports.clock import Clock

log = structlog.get_logger(__name__)


def publish_cycle(
    *,
    plan_id: str,
    gate_id: str,
    revision: int,
    disposition: OutputDisposition,
    output_reference: str | None,
    uow_factory: Callable[[], UnitOfWork],
    clock: Clock,
    forge: ForgePort,
    repo_path: Path,
    default_branch: str,
) -> str | None:
    """Record one output disposition, opening a real pull request when asked to
    and able. Returns the reference actually recorded."""
    if disposition != OutputDisposition.OPEN_PR:
        record_output_disposition(
            plan_id, gate_id, revision, disposition, output_reference,
            uow_factory(), clock,
        )
        return output_reference

    # 1. Read-only pre-check. No transaction, no writes.
    uow = uow_factory()
    plan = uow.plans.get(plan_id)
    cycle = plan.active_cycle
    if cycle is None:
        # Nothing to publish; let the aggregate produce the canonical refusal.
        record_output_disposition(
            plan_id, gate_id, revision, disposition, output_reference,
            uow_factory(), clock,
        )
        return output_reference

    branch = cycle_branch(cycle.id)
    evidence = [
        EvidenceLine(
            task_title=task.title,
            command=task.accepted_evidence.command,
            exit_code=task.accepted_evidence.exit_code,
            candidate_sha=task.accepted_evidence.candidate_sha,
        )
        for goal in cycle.goals
        for task in goal.tasks
        if getattr(task, "accepted_evidence", None) is not None
    ]

    # 2. Side effects, OUTSIDE any transaction.
    try:
        forge.push_branch(repo_path, branch)
        pull_request = forge.open_pull_request(
            head=branch,
            base=default_branch,
            title=render_pr_title(cycle.intent_summary),
            body=render_pr_body(
                cycle_id=cycle.id,
                cycle_intent=cycle.intent_summary,
                evidence=evidence,
                protected_paths=list(cycle.protected_paths),
            ),
        )
    except ForgeNotConfiguredError:
        # No forge bound: exactly today's behaviour, a reference the human typed.
        log.info("publication.no_forge", plan_id=plan_id, cycle_id=cycle.id)
        record_output_disposition(
            plan_id, gate_id, revision, disposition, output_reference,
            uow_factory(), clock,
        )
        return output_reference

    # 3. Record it, re-reading and re-guarding inside the transaction.
    log.info(
        "publication.pull_request_opened",
        plan_id=plan_id, cycle_id=cycle.id, number=pull_request.number,
    )
    record_output_disposition(
        plan_id, gate_id, revision, disposition, pull_request.url,
        uow_factory(), clock,
    )
    return pull_request.url
```

The attribute names `cycle.intent_summary`, `cycle.protected_paths`,
`task.accepted_evidence.command/.exit_code/.candidate_sha` and `task.title` must
be checked against the real aggregate in
`agent_orchestrator/domain/aggregates/planner_orchestrator.py` and
`domain/entities/`, and corrected to whatever the domain actually calls them.
**Do not add or rename any domain attribute** — read the code and adapt this
function to it.

- [ ] **Step 5: Rewire the route**

Replace the body of `publish_cycle_route` in
`agent_orchestrator/api/routers/plans.py:1194`:

```python
@router.post("/{plan_id}/publication", status_code=204)
def publish_cycle_route(
    plan_id: str,
    body: PublicationRequest,
    container: AppContainer = Depends(get_container),
) -> None:
    plan = container.new_unit_of_work().plans.get(plan_id)
    project_id = plan.project_id
    workspace = container.workspace_resolver.resolve(project_id)
    publish_cycle(
        plan_id=plan_id,
        gate_id=body.gate_id,
        revision=body.subject_revision,
        disposition=body.disposition,
        output_reference=body.output_reference,
        uow_factory=container.new_unit_of_work,
        clock=container.clock,
        forge=container.forge_for(project_id),
        repo_path=container.workspace_resolver.repository_path_for(
            container.project_repo.get(project_id)
        ),
        default_branch=workspace.default_branch,
    )
```

If `GitBranchWorkspace` has no public `default_branch`, use
`default_branch_of(repo_path)` from `repository_binding` instead.

- [ ] **Step 6: Run the tests**

Run: `cd backend && pytest tests/integration/test_publish_cycle.py tests/integration/test_cycle_evidence_api.py tests/integration/test_default_cyclic_execution.py -v`

Expected: PASS.

- [ ] **Step 7: Run the full backend suite**

Run: `cd backend && pytest`

Expected: PASS. This is the first task that touches the publication path every cyclic test walks through, so a full run is warranted here specifically.

- [ ] **Step 8: Quality gates and commit**

```bash
cd backend && ruff check agent_orchestrator tests --fix && mypy agent_orchestrator
cd .. && git add backend/agent_orchestrator/app/pr_body.py \
                 backend/agent_orchestrator/app/use_cases/publish_cycle.py \
                 backend/agent_orchestrator/api/routers/plans.py \
                 backend/tests/integration/test_publish_cycle.py
git commit -m "feat(app): publication opens the pull request, then records it

output_reference was free text a human typed — the one place someone
asserted something the system could not verify. For open_pr against a
bound forge it is now a fact the orchestrator produced.

The ordering is load-bearing: push and API call outside any transaction
(invariant #5), disposition recorded only after the PR exists. A forge
failure leaves the gate open with nothing half-written, and
retain_branch is still there.

An installation with no forge bound behaves exactly as before."
```

---

## Task 8: The four-step wizard

**Files:**
- Modify: `frontend/src/views/settings/ProjectsSection.tsx`
- Modify: `frontend/src/lib/queries.ts`
- Test: `frontend/src/views/settings/ProjectsSection.test.tsx` (create)

**Interfaces:**
- Consumes: `POST /api/projects/probe`, `POST /api/projects/{id}/clone`, `PUT /api/projects/{id}/forge`, and `ProjectBody.binding` — all from Tasks 2, 3, 4, 6.
- Produces: nothing consumed by a later task.

- [ ] **Step 1: Regenerate the API types**

Run: `cd frontend && npm run generate:api`

Expected: `src/types/generated/` gains the probe, clone and forge schemas. Commit
this before writing UI against it — CI fails on drift.

- [ ] **Step 2: Write the failing test**

Create `frontend/src/views/settings/ProjectsSection.test.tsx`, following the
patterns in `SetupSection.interaction.test.tsx` (same query-client wrapper, same
`msw`-or-fetch-mock approach — read it first and match it):

```tsx
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

describe('the project wizard', () => {
  it('never substitutes a scratch repository when the token is declined', async () => {
    // The phase's explicit prohibition: declining the delivery credential
    // changes only how work comes back, never which repository was named.
    const created = vi.fn().mockResolvedValue({ id: 'p1' });
    renderWizard({ onCreate: created });

    await userEvent.click(screen.getByRole('radio', { name: /clone a remote/i }));
    await userEvent.type(
      screen.getByLabelText(/repository url/i),
      'https://github.com/acme/widgets.git',
    );
    await userEvent.click(screen.getByRole('button', { name: /continue/i }));
    await userEvent.click(screen.getByRole('radio', { name: /leave it on a branch/i }));
    await userEvent.click(screen.getByRole('button', { name: /create project/i }));

    expect(created).toHaveBeenCalledWith(
      expect.objectContaining({
        repo_url: 'https://github.com/acme/widgets.git',
        binding: 'remote',
      }),
    );
  });

  it('reports a probe failure by kind rather than showing git stderr', async () => {
    renderWizard({
      probeResult: {
        binding: 'remote',
        reachable: false,
        problem_kind: 'needs_credentials',
        problem: 'fatal: could not read Username for ...',
        default_branch: null,
        resolved_path_preview: '/home/u/.orchestrator/projects/x/repos/ab12',
      },
    });

    await userEvent.click(screen.getByRole('radio', { name: /clone a remote/i }));
    await userEvent.type(screen.getByLabelText(/repository url/i), 'https://x/y.git');
    await userEvent.click(screen.getByRole('button', { name: /check/i }));

    expect(await screen.findByText(/needs credentials/i)).toBeInTheDocument();
  });
});
```

Write `renderWizard` as a local helper in the same file.

- [ ] **Step 3: Run it and confirm it fails**

Run: `cd frontend && npx vitest run src/views/settings/ProjectsSection.test.tsx`

Expected: FAIL — no such roles.

- [ ] **Step 4: Add the queries**

In `frontend/src/lib/queries.ts`, following the existing `useCreateProject`
pattern exactly, add `useProbeRepository`, `useCloneProject`, `useForgeBinding`
(a `useQuery`), and `useSetForgeBinding`.

- [ ] **Step 5: Build the wizard**

Replace `ProjectDialog` in `ProjectsSection.tsx` with a `ProjectWizard` holding
a `step` state of `'where' | 'probe' | 'delivery' | 'confirm'`. Requirements,
each covered by a test above or by the confirm-step copy:

1. **Where does the code live?** Three radios — *point at a local repository* /
   *clone a remote* / *create an empty one*. **No credential input on this step.**
   Sets `binding` to `local | remote | scratch` on the create body.
2. **Probe** (remote only): calls `useProbeRepository`, renders by
   `problem_kind` — `needs_credentials` → "needs credentials", `not_found` →
   "no repository there — check the URL", `unreachable`/`timeout` → "could not
   reach the host". Offers *Clone now* on success.
3. **How should work come back?** *Open a pull request for me* (reveals
   `owner/repo` + token fields, calls `useSetForgeBinding` after the project is
   created) or *leave it on a branch* (asks nothing). **Declining must not
   change `repo_url` or `binding`.**
4. **Confirm** — render this table verbatim:

| Choice | Where the code lands | How you get it |
|---|---|---|
| local | your own checkout | `git -C <path> diff <default>..cycle/<id>` |
| remote + PR | the orchestrator's clone | a pull request in `owner/repo` |
| remote, no PR | the orchestrator's clone | `git remote add orchestrator <path>` |
| empty | a scratch repository | demonstrates the flow; not code to keep |

Keep the existing `ProjectDialog` for the **edit** path — this task replaces
creation only, so an operator editing a name does not walk four steps.

- [ ] **Step 6: Run the frontend gates**

Run: `cd frontend && npx vitest run && npm run build`

Expected: PASS, clean build.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/views/settings/ProjectsSection.tsx \
        frontend/src/views/settings/ProjectsSection.test.tsx \
        frontend/src/lib/queries.ts \
        frontend/src/types/generated/
git commit -m "feat(frontend): a four-step repository wizard

The repository field was hinted 'Optional.', so nothing distinguished
'I have no repository yet' from 'I left the box blank'. Now the operator
names the binding, a remote is checked before it is bound, and the two
questions stay separate: where the code lives asks no credentials, and
declining the delivery token changes only how work comes back — never
which repository was named.

The confirm step states where the code lands and how to get it, which is
the thing that was previously only discoverable from /readiness."
```

---

## Task 9: Documentation, and the decision that promoted forge publication

**Files:**
- Create: `docs/guides/delivery.md`
- Modify: `docs/decisions/decision-log.md`
- Modify: `docs/architecture/capability-matrix.md`
- Modify: `CLAUDE.md`
- Modify: `ROADMAP.md`
- Modify: `SECURITY.md`

- [ ] **Step 1: Write the delivery guide**

Create `docs/guides/delivery.md` — it is picked up automatically by the console's
`import.meta.glob` over `docs/guides/*.md`, so there is exactly one copy.
Cover: the three bindings and where code lands in each; how to bind a GitHub
token and what scope it needs (`repo`, or fine-grained with Contents +
Pull requests write); what happens at the publication gate with and without a
forge; and the standing limit that the orchestrator opens a pull request and
never merges one.

- [ ] **Step 2: Record the decision**

Append an entry to `docs/decisions/decision-log.md`: authenticated forge
publication promoted out of the deferred list into Phase 8 / P8.1 on 2026-08-02,
with the reason (the wizard's token step has no consumer otherwise), the bounds
(GitHub only, opens but never merges, pushes only `cycle/<id>`), and the note
that it required **no domain un-freeze** because the binding lives in the
project-scoped config store. Number it following the existing sequence.

- [ ] **Step 3: Correct the docs that now describe the old behaviour**

- `CLAUDE.md` — the Git Workspace Rules say *"GitHub PR output is DEFERRED (stub seam behind the Workspace port — no authenticated forge/PR-write port exists yet)"*. Replace with the real state: a `ForgePort` in `app/`, GitHub adapter, opens a PR and never merges, `NoForge` fallback.
- `SECURITY.md` — add the forge token: per project, envelope-encrypted in the secret store, needs push access, and the orchestrator pushes only `cycle/<id>`.
- `ROADMAP.md` — mark P8.1 delivered under Phase 8 with what shipped.
- `docs/architecture/capability-matrix.md` — confirm all five new routes are classified.

- [ ] **Step 4: Verify the docs contracts**

Run: `cd backend && pytest tests/unit/test_capability_matrix.py tests/unit/test_fixture_docs_contract.py -v`

Expected: PASS.

- [ ] **Step 5: Full verification before the PR leaves draft**

```bash
cd backend && ruff check agent_orchestrator tests && mypy agent_orchestrator && pytest
cd ../frontend && npm run build && npx vitest run && npm run generate:api && git diff --exit-code src/types/generated/
```

Expected: all green; the last command must produce no diff, or CI fails on type drift.

- [ ] **Step 6: Commit**

```bash
git add docs/ CLAUDE.md ROADMAP.md SECURITY.md
git commit -m "docs: record P8.1, and correct what said PR output was deferred

CLAUDE.md and SECURITY.md described a system with no authenticated forge
port. A doc contradicting the code is a bug in the doc, fixed in the same
PR that made it wrong.

The decision log records the promotion out of the deferred list, its
bounds, and that it needed no domain un-freeze."
```

---

## Self-review

**Spec coverage.** §4.1 → Task 2. §4.2 → Tasks 3 and 4. §4.3 → Task 1. §4.4 → Task 5. §4.5 → Task 6 (`binding.py`, `SecretRef.for_forge`, `forge_for`). §4.6 → Task 6 (`verify_github_token` + the three routes). §4.7 → Task 7. §4.8 → Task 8. §5 error codes → Task 5 step 6. §6 testing — unit, `FakeForge`, integration, scripted transport, F2 regression, frontend: Tasks 1, 5, 6, 7, 8. The **opt-in real-GitHub smoke** in §6 is deliberately not a task: it needs a live repository and a human's token, so it is recorded as follow-up rather than planned as a step someone could fake.

**Placeholders.** None. Three steps say "check the real name and adapt" (config-store `delete`, secret-store `delete`, the domain attribute names in Task 7 step 4). Those are deliberate: guessing a name in a plan is worse than instructing the implementer to read it, and Task 7 in particular must not invent a domain attribute.

**Type consistency.** `PullRequestRef(url, number)`, `push_branch(repo, branch)`, `open_pull_request(*, head, base, title, body)`, `ForgeRequestFailedError`, `FakeForge(fail_on=...)`, `SecretRef.for_forge`, `read_binding/write_binding/clear_binding`, `forge_for(project_id)`, `publish_cycle(...)` — each defined once and used with the same signature everywhere after.
