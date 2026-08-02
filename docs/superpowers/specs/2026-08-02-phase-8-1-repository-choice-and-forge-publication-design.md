# P8.1 — repository choice, and publication that really opens a pull request

**Status:** accepted design. Implementation follows in the plan of the same date.
**Date:** 2026-08-02
**Phase:** 8 — closing the demonstrability gaps, first deliverable.
**Branch:** `phase-8-demonstrability`

---

## 1. What this closes

Phase 8's deliverable list names *"the repository-choice wizard — clone a
remote, point at a local repository, or create an empty one"*, and adds a
constraint:

> the two questions it must keep separate: *where the code lives* needs no
> credentials, and *whether we can push and open a PR* does. Declining the token
> must downgrade the delivery method, never silently substitute a scratch
> repository for the project the operator named.

That constraint presumes a delivery method that a token *changes*. Today none
exists: `OutputDisposition.OPEN_PR` records that a human opened a pull request,
and `output_reference` is free text they type
(`planner_orchestrator.py:1182`). **Authenticated forge publication is
therefore promoted out of the deferred list into this deliverable** — decided
2026-08-02. Building the token step first and its consumer later would ship a
credential nothing reads, which is the workaround the constraint exists to
forbid.

This also answers open question #3 of
[`2026-08-02-work-delivery-ux-analysis.md`](2026-08-02-work-delivery-ux-analysis.md):

> **Should `output_reference` stay free text?** It is the one place a human
> asserts something the system cannot verify.

After this, for `open_pr` against a bound forge, it is a fact the orchestrator
produced.

## 2. Findings that shape the design

Read from the code on 2026-08-02, before any of it was designed.

**F1 — a remote is cloned lazily, inside the worker.**
`ProjectWorkspaceResolver._materialize_remote`
(`infra/git/project_workspace.py:126`) runs only from `resolve(project_id)` —
the first time a worker touches the project. `validate_repo_url` syntax-checks a
remote and deliberately never touches the network. So a typo'd, private, or
unreachable remote fails as a `CalledProcessError` *during a cycle*, not at
setup, which is exactly the shape of failure that strands a first-time user.

**F2 — that clone can hang indefinitely.** It runs
`subprocess.run(["git", "clone", ...], capture_output=True)` with no
`GIT_TERMINAL_PROMPT=0`. A private `https://` remote makes git prompt for a
username, and there is no tty to answer it. The worker blocks while holding a
goal lease. This is a defect independent of the wizard and is fixed here.

**F3 — "create an empty repository" has no representation.** It is
`repo_url = null`, and the field's hint in `ProjectsSection.tsx:137` is
literally `"Optional."`. Nothing distinguishes *I have no repository yet* from
*I left the box blank* — the silent scratch substitution the phase warns about,
present in the current UI.

**F4 — the delivery consequence is invisible at the moment of choice.** Which
binding the operator picks decides whether the work lands in their own checkout
or in `$ORCHESTRATOR_HOME/projects/<id>/repos/<sha256[:16]>`, and that is
discoverable only later, from `GET /api/projects/{id}/readiness`.

## 3. Non-goals

Stated first, because each is a thing a reviewer will reasonably ask for.

- **No merging.** The orchestrator opens a pull request and never merges one.
  `OutputDisposition.MERGE` stays a recorded human claim.
- **No writes to the default branch.** Only `cycle/<id>` is pushed. This is R1
  of the delivery analysis and the guarantee the whole branch ladder exists to
  make.
- **No generic forge abstraction.** GitHub only. Guessing GitLab or Gitea
  semantics with no user asking is the completeness the roadmap's scope
  discipline forbids; the port makes a second adapter cheap when someone does.
- **No domain change, and therefore no un-freeze.** See §4.5.
- **No credential prompt during a run.** Every credential question is answered
  at setup, verified at setup, and fails at setup.

## 4. Design

### 4.1 Name the binding instead of inferring it

`BindingKind` (`local | remote | scratch`) already exists in
`infra/git/repository_binding.py`. Today it is *derived* from the shape of a
string. The wizard makes the operator **name** it, and the API checks the name
against what the URL actually is.

`ProjectBody` (`api/routers/reference.py:300`) gains:

```python
binding: Literal["local", "remote", "scratch"] | None = None
```

When present it must agree with `validate_repo_url`'s derivation; disagreement
raises `ProjectBindingInvalidError` → 422 `PROJECT_BINDING_INVALID`, already in
the error table. Naming `remote` with a blank URL is refused rather than
quietly becoming a scratch repository — F3 closed at the API, not only in the UI.

It stays **optional** so the five fixtures, `run-cycle.sh`, and the integration
suite keep working unchanged. Inference remains the fallback; this is additive.

### 4.2 The probe is its own endpoint, not part of `create`

`repository_binding.py`'s module docstring records a deliberate decision
*against* a network probe at write time:

> a create request must not block on a slow or unreachable host, and a
> repository reachable now may not be at execution time, so a network probe
> would cost a timeout and buy very little

That reasoning is still correct for write-time validation and wrong for a
first-run wizard, which is a different moment with a human watching. Rather
than reverse it, the two are separated:

**`POST /api/projects/probe`** — read-only, writes nothing, needs no project to
exist. Body `{repo_url}`; response:

```json
{ "binding": "remote", "reachable": true, "default_branch": "main",
  "resolved_path_preview": "/home/u/.orchestrator/projects/<id>/repos/<sha>",
  "problem": null, "problem_kind": null }
```

Implemented as `probe_remote()` in `repository_binding.py`, running
`git ls-remote --heads --` under a 5s timeout with `GIT_TERMINAL_PROMPT=0`,
`GIT_ASKPASS=echo`, and `GIT_SSH_COMMAND` carrying `BatchMode=yes` so no probe
can ever block on a prompt. `problem_kind` is one of
`needs_credentials | not_found | unreachable | timeout`, because the wizard's
next step differs per kind: needing credentials is a token problem, not-found
is a typo.

`create` never calls it, so creating a project stays fast and works offline.

**`POST /api/projects/{id}/clone`** — explicit and idempotent (returns the
existing clone if present). Materializes a remote *now* so an operator can pay
the clone cost at setup instead of inside their first cycle. Returns
`{resolved_path, default_branch}`.

### 4.3 The clone-hang fix (F2)

`_materialize_remote` gets the same environment guard as the probe. Regression
test: a clone against a URL that would prompt terminates with a classified
error rather than blocking. This lands regardless of the rest of the phase.

### 4.4 The forge port

`app/forge_port.py`, beside the existing `app/sandbox_port.py` — an
infrastructure-facing port that the frozen domain never sees, exactly the call
the roadmap makes for `ProjectEnvironment`.

```python
@dataclass(frozen=True)
class PullRequestRef:
    url: str
    number: int

@runtime_checkable
class ForgePort(Protocol):
    def push_branch(self, repo: Path, branch: str) -> None: ...
    def open_pull_request(
        self, *, head: str, base: str, title: str, body: str
    ) -> PullRequestRef: ...
```

Push belongs here rather than on the Workspace port because the Workspace
port's contract is local git only, and an authenticated push spends the
forge's credential, not the workspace's. Adapters:

- **`infra/forge/github.py::GitHubForge`** — REST against `api.github.com`.
- **`infra/forge/no_forge.py::NoForge`** — the permanent fallback, not a
  placeholder, on the same principle as `NoSandbox`. It raises a typed
  `ForgeNotConfiguredError` that the publication path renders as the manual
  branch instructions already served today.

`httpx` is declared as an explicit runtime dependency. It currently reaches
runtime only transitively through `openai`, which is not a contract.

### 4.5 The forge binding lives in config, not the domain

`ProjectDefinition` is a frozen-domain entity carrying `id`, `name`,
`repo_url`. Adding forge fields to it would require a decision-log entry and an
un-freeze.

It does not need to. The config store is already two-tier — `ConfigTable`,
scope `'orchestrator'` or a project id (`infra/db/tables.py:511`) — and a
project id is already documented as a config scope. The binding is therefore
three project-scoped keys:

| key | value |
|---|---|
| `forge.provider` | `github` (only value today) |
| `forge.repository` | `owner/repo` |
| `forge.token_ref` | `secret://forge/<project_id>` |

The token itself goes in the existing secret store: `SecretRef.for_forge(project_id)`,
envelope-encrypted, `resolve()` still the single decryption point in the
codebase, never logged. Per-project rather than global, because two projects can
live on different accounts and one credential spanning every project is the
wrong blast radius for an unsandboxed local tool.

**Consequence: P8.1 requires no domain un-freeze.** Worth stating because the
phase's later deliverables may not be so lucky.

### 4.6 Verify the token at save time

`PUT /api/projects/{id}/forge` stores the binding and, before storing, calls
`GET /repos/{owner}/{repo}` with the token. That single call confirms three
things at once: the repository exists, the token reaches it, and
`permissions.push` is true. A token that cannot push is refused at setup with
the reason, rather than at the publication gate twenty-five minutes into a
cycle.

`DELETE /api/projects/{id}/forge` removes the binding and the secret.
`GET` returns the binding **without the token** — provider, repository,
`token_verified_at`, and the login the token resolved to.

### 4.7 Publication: the side effect runs outside the transaction

`record_output_disposition` (`app/use_cases/cyclic_planning.py:325`) currently
does everything inside one `with uow:`. Pushing and calling the GitHub API
inside that transaction would violate architectural invariant #5 — side effects
happen outside transactions, and finalize transactions re-read and re-guard.

So for `open_pr` with a forge bound, a new use case `publish_cycle` in
`app/use_cases/cyclic_planning.py` owns a three-step flow and the router stays
thin (invariant #8) — it resolves the forge adapter from the container and
calls one function:

1. **Read-only pre-check**, no transaction: load the plan, confirm the gate is
   open at the given revision, confirm the cycle branch exists. Cheap refusal
   before anything external happens.
2. **Outside any transaction:** `push_branch(repo, "cycle/<id>")`, then
   `open_pull_request(head=…, base=<default branch>, title=…, body=…)` with the
   evidence document rendered into the body — accepted commands and exit codes,
   candidate and test commit SHAs, promotion refs. This is the product's own
   argument, in the place a reviewer will read it.
3. **Then** `record_output_disposition(...)` exactly as it works today, re-reading
   the plan and re-guarding gate id and revision, with the real PR URL as
   `output_reference`.

On failure at step 2 the gate stays open, the forge's own message reaches the
operator, and `retain_branch` remains available. Nothing is half-recorded: the
disposition is written only after the PR exists.

If no forge is bound, `open_pr` behaves exactly as it does today — a recorded
claim with operator-typed `output_reference`. Existing behaviour is not
removed, and the gate labels the two cases differently.

### 4.8 The wizard

`ProjectsSection.tsx`'s two-field `ProjectDialog` becomes a four-step wizard.
The two questions stay separate, as the phase demands.

1. **Where does the code live?** Three cards — point at a local repository /
   clone a remote / create an empty one. **No credentials asked at this step.**
   Each card states where work will land.
2. *(remote only)* **Probe.** Calls `POST /api/projects/probe` and reports the
   result by `problem_kind`. Offers "clone now" on success.
3. **How should work come back?** *Open a pull request for me* — asks for a
   token, verifies it (§4.6) — or *leave it on a branch*, which asks nothing.
   **Declining changes only this answer.** Step 1's repository is never
   substituted; that is the phase's explicit prohibition, and a frontend test
   asserts it by driving the decline path and checking the created project's
   `repo_url`.
4. **Confirm.** States where the code lands and how it comes back:

| Choice | Where the code lands | How you get it |
|---|---|---|
| local | your own checkout | `git -C <path> diff <default>..cycle/<id>` |
| remote + PR | the orchestrator's clone | a pull request in `owner/repo` |
| remote, no PR | the orchestrator's clone | resolved path + `git remote add orchestrator <path>` |
| empty | a scratch repository | demonstrates the flow; not code to keep |

## 5. Error handling

New codes in the single table `api/exceptions.py::_STATUS_BY_CODE`; no
try/except in routers, per invariant #8.

| code | status | when |
|---|---|---|
| `FORGE_NOT_CONFIGURED` | 422 | `open_pr` asked to really open one with no binding |
| `FORGE_AUTH_FAILED` | 422 | token rejected, or lacks push permission |
| `FORGE_REPO_NOT_FOUND` | 422 | `owner/repo` does not resolve for this token |
| `FORGE_PUSH_FAILED` | 502 | push reached the remote and was refused |
| `FORGE_REQUEST_FAILED` | 502 | the forge API failed or was unreachable |

`PROJECT_BINDING_INVALID` (422) already exists and covers §4.1's disagreement
check and the probe's classified failures.

## 6. Testing

- **Unit** — `binding` agreement checks; `probe_remote`'s classification of
  each `problem_kind`; the PR body renderer against a known evidence document.
- **`FakeForge`** in `app/testing/fakes.py`: records pushes and PRs, scriptable
  to fail at either step. Used to prove the gate stays open and nothing is
  recorded when step 2 fails.
- **Integration** — the publication path end to end against `FakeForge` on real
  SQLite: PR opened, then disposition recorded with the returned URL; and the
  failure case, asserting the gate is still open and `output_disposition` is
  still `None`.
- **`GitHubForge` against a scripted HTTP transport** — no network, exercising
  the real request construction, the 401/403/404 mappings, and that no token
  ever appears in a log record.
- **Regression for F2** — a clone against a prompting URL terminates rather
  than blocking.
- **Frontend** — the wizard's decline path (step 3 declined leaves step 1's
  `repo_url` intact), and the four confirm-step statements.
- **A real-GitHub smoke behind an opt-in marker**, like the existing `llm`
  marker. Never in CI.

## 7. Open decisions, deliberately deferred

- **PR title and body template.** Drafted from the evidence document; the exact
  wording is a copy decision for implementation, not an architectural one.
- **Draft PRs.** GitHub supports opening a PR as a draft. Plausibly the right
  default for agent output, but no evidence either way yet; not built.
- **Re-publishing a cycle whose PR was closed.** Out of scope: the gate records
  one disposition per cycle, and `start_replan` is the existing path.

## 8. Sequencing

One PR on `phase-8-demonstrability`, in this order, each step green before the
next:

1. F2 clone-hang fix + regression test (independent, smallest, ships value alone)
2. `binding` agreement check + `POST /api/projects/probe` + `POST …/clone`
3. `ForgePort`, `NoForge`, `FakeForge`, error codes
4. `GitHubForge` + the forge binding endpoints + token verification
5. Publication rewiring (§4.7)
6. The wizard (§4.8) + regenerated API types
7. Docs: `docs/guides/` forge setup, capability-matrix rows, ROADMAP Phase 8
   status, decision-log entry recording the promotion out of the deferred list
