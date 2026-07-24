# ROADMAP

Everything designed or planned but **not yet implemented**, in priority order. Each item names its origin so you can read the full reasoning:

- **[EVO]** — the 2026-07-06 evolution plan ([archived](docs/history/planning/2026-07-06-orchestrator-evolution-plan-fable-5.md)).
- **[MRF]** — the master roadmap the integration was executed against ([archived](docs/history/planning/2026-07-02-master-roadmap-final-fable-5.md)).
- **[LEG]** — pre-refactor features shelved with designed seams ([docs/legacy/pre-refactor-backend.md](docs/legacy/pre-refactor-backend.md)).
- **[LIVE]** — defects/gaps verified by the first real end-to-end plan ([review](docs/history/analyses/2026-07-13-first-live-plan-review.md), [strategy](docs/history/planning/2026-07-13-execution-domain-refactor-strategy.md)).
- **[PR20]** — findings from the PR #20 review that landed the cyclic-lifecycle refactor (decisions 44–48).
- **[CI]** — pending CI/verification hardening surfaced while getting PR #20 green.
- **[WALK]** — findings from the 2026-07-19 live plan walkthrough (issues #28, #30–#32; fixes in PR #29 and this branch).

Verified defects backing several items below are documented in
[docs/architecture/known-issues.md](docs/architecture/known-issues.md) — read that
file, don't duplicate it here.

**Current architecture** (context for everything below): a long-lived root
`ProjectPlan` contains `Cycle`s, the finite delivery unit. Each cycle moves
`IntentProposal` (versioned, exact-revision review gate) → `CycleDraft`
(ordered goals, stable local keys, no forward-dependency edges, second
exact-revision gate) → active `Cycle`. The old nine-phase `PlanPhase` machine
is legacy-compat only (see known-issues.md); do not describe it as current.

---

## Now — CI and merge hygiene [CI]

Blocking or near-blocking issues on the current PR.

### 1. Verification environment determinism — watch item
Two determinism bugs in `LocalVerificationExecutor` were found and fixed on
this branch: a login-shell PATH reset breaking interpreter resolution, and
interpreter/test-cache byproducts (`__pycache__`, `.pytest_cache`, `*.pyc`)
leaking into frozen verification bundles. Both are fixed; keep watching for
further machine-dependent assumptions (locale, timezone, ambient env vars) the
executor might still inherit from the host shell.

## Now — agent isolation and prompt fidelity [WALK]

The walkthrough proved agents escape cooperative isolation: worktree + cwd is a
convention, not a boundary. The post-run guard (this branch) is DETECTION; these
items are prevention, cheapest first.

### 2. Pointer-free attempt workspaces
The per-attempt git worktree's `.git` is a pointer file naming the main repo's
absolute path — the second breadcrumb (the first, full env inheritance, was
closed by the allowlisted child env in the 2026-07-20 accelerate run). Evaluate
`git clone --shared` (or scissor the pointer) for attempt workspaces so no
on-disk path names the main repository; keep the branch/merge semantics
identical. Decide against the worktree-prune/audit machinery decision 45 added
before changing the mechanism. Evaluation done 2026-07-20: gitfile scissoring
is ruled out (corrupts worktree bookkeeping); the candidates are
`GIT_DIR`+per-attempt-index redirection (keeps in-process `--no-ff` merges,
requires branch-ref-only audit) vs `git clone --shared` (push-based merges,
alternates still name the main repo). Both obsolete decision 45's
worktree-based audit — implementation awaits an explicit decision.

## Now — test foundation and boundary cleanup [PR20]

Sequencing matters: strengthen tests before refactoring domain/app code so the
refactors below have a safety net.

### 3. Clarify domain/application boundaries
(Item "harden the test foundation" closed 2026-07-20: API-layer coverage landed
in PR #26/#27, the SSE streaming gap is closed by a live-socket uvicorn test,
and `src/app/verification.py` was confirmed single-owner — its charter is
frozen test-BUNDLE validation (pure, handler-only caller), not command
execution, which lives in `VerificationExecutor`.)

Decision briefs for the bullets below were prepared in the 2026-07-20
accelerate run (router-logic inventory with line refs; `app/ports.py` is a
justified re-export facade — collapsing it would invert the dependency arrow;
`observations.py` currently has zero live consumers). The decisions remain
open:
- Audit `src/api/routers/plans.py` (1088 lines) for embedded use-case logic
  (branching, validation, orchestration) that belongs in `src/app/`; routers
  are supposed to be thin per the architectural invariants.
- Decide whether app-layer ports (`src/app/ports.py`, 158 lines) are justified
  as a separate layer from domain ports, or whether they should collapse into
  the domain ports package.
- Decide whether `src/app/observations.py` (201 lines) is still needed, or
  whether domain telemetry + domain events (outbox/agent_events) already
  cover its job.

### 4. Refactor orchestration (after 3 lands)
- Decompose `src/domain/aggregates/planner_orchestrator.py` (882 lines) — the
  single-authority aggregate is correct as a boundary, but its size makes
  review and change risky; split by responsibility (transitions vs.
  navigation vs. gate logic) without breaking the "only caller of
  Goal/Task transitions" invariant.
- Remove legacy/backward-compat code (e.g. the compat branches in
  `src/app/use_cases/advance_plan.py` and similar call sites) — the test
  hardening that protects the behavior being deleted landed 2026-07-20.
- Formalize abrupt pause/resume semantics: define precisely when a pause must
  interrupt a running task attempt vs. merely stop new claims, and what
  "paused goal" means for a goal mid-promotion. Decision 44's Git-promotion
  reservation already carves out that pause remains legal during a
  reservation — this item is the general policy the reservation is a special
  case of.

### 5. Refactor handlers, in dependency order (after 3–4)
`execution_handler.py` (1249 lines) first, then `planning_handler.py` (514
lines) — execution is upstream of planning in the dependency graph and carries
more risk. Share only mechanisms proven common to both (not speculative
abstraction). Refactoring prompts already exist from the PR #20 review; do not
start until item 3's decisions land — the API-layer safety net exists; the point is
a safety net before touching 1700+ lines of handler code.

## Next — tooling and documentation consolidation [PR20]

### 6. DB-schema-inspection → diagram tool
Idea surfaced during the migration 0009 review: a small tool that inspects the
live SQLite schema and emits a diagram (tables, FKs, indexes) so
`docs/architecture/data-model.md` can be checked against the DB instead of
hand-maintained. Evidence gate checked 2026-07-20: the doc HAD drifted (two
migrations, 0009+0010 — two tables and ~20 columns) and was hand-fixed in the
same run (~30 min). One lapse doesn't evidence recurring drift; the tool stays
unbuilt unless the doc drifts again.

### 7. Oversized graphify skill review
Tracked separately from this roadmap: the graphify skill review left comments
against outdated diff locations (the file moved under later commits). Not an
architecture item — a skill-repo hygiene follow-up.

## Then — operational visibility [LIVE / PR20]

10. **Truthful usage scopes [LIVE]** — split planner LLM usage from
    child-agent usage; add coverage/provenance fields and reasoning tokens.
    Partially landed: decision 45 gives planner/child/combined coverage with
    "unavailable" distinct from zero — re-verify remaining gaps against
    known-issues.md before treating this as done.
11. **Run timing and pause visibility [LIVE]** — expose run_id, started time,
    elapsed, last heartbeat, deadline, and whether pause is merely waiting for
    the current run boundary. Decision 45 hydrates attempt-history before SSE
    and decision 46 added typed cycle/proposal/draft/gate/block exports on
    plan detail; known-issues.md still flags the read model exposes active
    run start rather than the promoted lease deadline — that gap is this
    item's remaining scope.
12. **Dead-letter / operator quarantine policy** — known-issues.md still lists
    a malformed plan that raises before any save as reclaimable-first by
    oldest `updated_at`; no quarantine policy for repeated unexpected
    exceptions yet.
13. **Execution ledger `run_kind` column** — attempts have global UUIDs and
    monotonic absolute numbers, but `run_kind` isn't promoted to a dedicated
    SQL column (known-issues.md).
14. **`workspace gc` CLI for dead branches** — decision 45 landed conservative
    worktree pruning/audit at worker startup, closing the original "leaks
    forever" failure mode; an operator-triggered `workspace gc` for branches
    left over from long-dead plans may still be worth adding. Re-verify
    against known-issues.md's "not yet automated everywhere" note before
    committing to scope.
15. **Retention** — `outbox`, `agent_events`, `plan_chat_messages`, and now
    authoritative test-checkpoint branch refs (known-issues.md) grow forever.
    Add a `db prune` CLI command plus a checkpoint-ref retention policy.
16. **SSE durability** — SSE is bounded and non-durable; reconnect relies on
    client refetch. Relay/event-table retention remains operational work
    (known-issues.md).
16a. **Frontend live runtime-log viewer** — the backend now exposes the RAW
    per-attempt agent stdout/stderr as a live SSE tail at
    `GET /api/plans/{id}/attempts/{attempt_id}/log/stream` (distinct from the
    `/api/events` telemetry feed; `follow_attempt_log` in `process_supervisor.py`,
    with rotation-reset + `Last-Event-ID` resume). Still needed: a frontend
    component that opens this stream and renders live stdout/stderr for an
    attempt in the Activity/Agents view (replacing the current poll of the
    snapshot `…/log` endpoint), plus `npm run generate:api` to pick up the new
    route in the generated client.

## Then — project ownership and output isolation [LIVE]

17. Require a `project_id` on plans and route through a project workspace
    resolver.
18. Store repositories under the orchestrator home at
    `projects/<immutable-project-id-or-slug>/repo`; do not use a mutable
    display name as the filesystem identity.
19. Define migration or quarantine behavior for the legacy global
    workspace-repo.
20. Add project delete guards for active plans and dual-project isolation
    tests.
21. Add an explicit plan output disposition so DONE says whether the branch
    was merged, opened as a PR, kept, or discarded. Partially informed by
    known-issues.md: `open_pr`/`merge` dispositions already record the
    reference of an externally-completed operation; there is still no
    authenticated forge port — see the deferred-features table.

## Later — evidence-gated capability work

Take these up only when real usage demonstrates the need.

22. **Multi-worker deployment, documented + truth-tested** [EVO] — add a
    two-worker truth test and operator docs now that decision 45's stale-claim
    startup handling exists.
23. **Registry-defined execution profiles and coverage preflight** [LIVE] —
    let users create stable execution-role profiles and capability policies in
    the registry instead of keeping the TDD role vocabulary in
    `_ROLE_CAPABILITY`. Contracts should reference versioned role/profile ids;
    the settings UI should show a role × task-capability coverage matrix;
    cycle review should warn about uncovered combinations before enrichment;
    registry edits should expose their impact on active/future contracts
    without silently rebinding work. Decision 47 already preserves explicit
    role-capability checks and transactional retry binding — build on that,
    don't replace it.
24. **Worker/scheduler health surface** [MRF] — expose last-heartbeat, current
    claims, and restart counts (a `/api/workers` endpoint). The lease is the
    recovery *mechanism*; this is *visibility*.
25. **Launcher / OS supervision** [MRF] — a thin, idempotent supervisor
    (systemd or process manager) that restarts a dead worker; the lease
    handles the takeover. Document failure modes; no distributed consensus.
26. **pi NDJSON streaming** [MRF] — partially landed 2026-07-20: the pi
    runner now runs `--mode json`, the NDJSON stream tails into the bounded
    per-attempt runtime log (atomic rotation; readable mid-run via
    `GET /plans/{id}/attempts/{id}/log`), tool/usage events are promoted to
    agent_events, and the final assistant message becomes the task output
    (`src/infra/runtime/pi_protocol.py`). Remaining scope is only the full
    rpc/stdio handshake if bidirectional control is ever needed.
27. **Redis claim path** [MRF] — swap the SQLite lease transport behind the
    repository port *only if* multi-machine workers become real. Deliberately
    unnecessary for local-first. Re-evaluated 2026-07-22 alongside goal-level
    parallelism landing (item 23's old slot, now implemented — see
    [ADR-001](docs/decisions/adr-001-concurrency-lease.md)): the SQLite
    `goal_leases` table already delivers real cross-*process* concurrency on
    one machine, which is what actually needed solving; Redis only becomes
    the right answer for cross-*machine* deployment, a different problem
    this system's single-SQLite-file persistence model doesn't attempt to
    solve either. Still deliberately unnecessary until multi-machine is the
    actual goal. Re-confirmed 2026-07-23 (domain unfreeze #14, symmetric
    per-goal leases + the in-process goal-worker pool): real single-process
    concurrency needed no new coordination primitive either, just removing
    the plan-level lease's execution-dispatch privilege — another point in
    favor of "this problem was never actually about the transport."
28. **CI pipeline split** [MRF / CI] — per-PR: unit + integration + dummy e2e +
    ruff/mypy; nightly/merge-only: the paid real-model smoke. Still open —
    the split matters, don't burn money per push.
29. **Frontend E2E (Playwright)** [MRF, [archived plan](docs/history/planning/2026-06-15-playwright-e2e-plan-deferred.md)] — one full-cycle browser walk against the dry-run stack; the archived plan targets the old API and needs rewriting against the current routes.
30. **Unified telemetry store** [MRF] — one queryable persistence for outbox +
    agent_events + API request logs. Build on the existing two streams; **no
    second event system**.
31. **Proactive goal-scope-disjointness guard** [MRF, ADR-001 follow-up] —
    goal-level parallelism (implemented 2026-07-22, domain unfreeze #13;
    made fully symmetric 2026-07-23, domain unfreeze #14) ships with only a
    REACTIVE safety net for concurrent goals touching overlapping files (the
    existing `goal_promotion_failure` block, hit at git-merge time — now
    per-goal, see `Plan.goal_blocks`, so one goal's merge conflict no longer
    stalls unrelated goals either). A proactive guard would check, at
    goal-enrichment time, whether a goal's frozen `allowed_scope` overlaps
    any OTHER concurrently-reachable goal's (no dependency edge either way)
    and reject/re-prompt before either ever runs. Deliberately deferred, not
    an oversight: the failure-UX decision (auto-reprompt the reasoner vs.
    open a block vs. just log) isn't settled, and "could legitimately run
    concurrently" is a static approximation that could produce false-positive
    friction on a deployment that never actually contends (single worker
    pool, e.g.) — needs real usage evidence first, same as the other
    evidence-gated items on this list. Unfreeze #13's in-process goal-worker
    pool (`max_concurrent_goals`, default 4, not load-tested) makes real
    contention MORE likely to actually occur in a live deployment than #12's
    additive shape did, which raises the value of real usage evidence here
    but still doesn't settle the failure-UX question on its own — the
    dependency stays open.

32. **Devcontainer runtime parity** [WALK] — the live container carries
    runtimes the `.devcontainer` config never installs (ad-hoc installs, lost
    on every rebuild): `codex` (the acceleration flow's primary implementer),
    `grok`, `mimo`, the `gh` CLI (used for all PR/issue automation), and
    `bubblewrap`. Add them to the Dockerfile with pinned versions; persist
    their auth/config dirs (`~/.codex`, `~/.grok`, `~/.mimocode`, gh auth)
    via mounts like the existing `~/.orchestrator` pattern; for bubblewrap
    also add the container `runArgs` (seccomp profile / userns capability)
    without which it cannot create namespaces at all. While there: drop the
    stale `AGENT_MODE` terminal env (the config key replaced it) and align
    the image's Python with CI (3.12). `gemini` remains a factory
    `runtime_type` with no binary anywhere — decide to provision or delist it.
33. **True FS sandboxing per attempt (bubblewrap)** [WALK] — item 32's
    devcontainer fix (2026-07-22: seccomp profile + AppArmor policy for safe
    nested bubblewrap) unblocked this: bwrap 0.11.0 now creates the
    unprivileged user namespace successfully in this container (re-verified
    live, 2026-07-22 — `--unshare-user` + a bound writable worktree + real
    read-only enforcement elsewhere, `pi`/`claude` both resolve inside the
    sandbox). The `Sandbox` port + `NoSandbox` adapter landed the same day
    (peer of `AgentRunner`, explicitly not a domain concept — the frozen
    domain and `ExecutionHandler` never see this type; `wrap(cmd, policy) ->
    cmd` + `probe()`, consumed blindly by the CLI runners/
    `supervise_process`; probe status surfaces through `GET
    /api/runner/status` alongside the binary probes). Remaining scope is
    purely the real `BubblewrapSandbox` adapter: bind the attempt worktree
    rw, toolchain ro, tmpfs HOME with the CLI's auth copied in, network on;
    config-gated (default off, mirrors `agent_runner.mode`) so nothing
    changes for an existing real-mode setup until explicitly enabled; loud
    `sandbox=disabled` fallback when the probe fails, never a silent
    downgrade or a refusal to run. Items 2–3 (pointer-free workspaces, a
    scrubbed env) remain open hardening follow-ups, not blockers for a first
    `--ro-bind / /`-shaped cut.
34. **Per-role model quality bindings** [WALK] — the free reasoning model
    follows task descriptions over role instructions; the registry already
    binds provider/model per agent, so route the test_author role to a
    stronger model once real usage justifies the spend. Pairs with the
    registry-profiles item above.

## Deferred features — shelved with designed seams [LEG]

Documented in full, with reintroduction designs, in [docs/legacy/pre-refactor-backend.md](docs/legacy/pre-refactor-backend.md):

| Feature | Seam that preserves it |
|---|---|
| GitHub PR gate / authenticated forge port (orchestrator opens PRs, humans merge) | The `Workspace` port — `open_pr`/`merge` dispositions already record external references (known-issues.md); no authenticated push/PR write exists yet |
| Project spec governance (`propose → diff → apply`) | Two-tier config + the `projects` table; also blocks a persisted `ProjectSpec` for cycle-wide verification commands (known-issues.md) |
| Decision gate / decision history | Genuine whitespace — design preserved in the legacy doc |
| Env provisioner (uv/Bun) + framework questionnaire | Config fields first, provisioning later [MRF] |
| Repository indexing / symbol graph / context packaging | Never built; idea preserved |
| Replay & audit tooling (reconstruct a run from events) | Outbox + agent_events already carry the data |

## Do-not-do list [EVO]

Tempting improvements explicitly rejected — with reasons — so they aren't re-litigated by default:

- **Temporal / DBOS / Celery / Redis now** — this is a local-first tool whose deployment story is one SQLite file; a workflow engine adds a server dependency and makes the human-gated cycle machine *harder* to test. Re-evaluate only if a multi-executor pool starts re-inventing workflow versioning.
- **Task-level parallelism now** — no throughput evidence; it breaks the plan-document CAS model for a speculative gain.
- **Splitting the plan JSON into relational goal/task rows** — the single document + the dual-backend truth tests are the system's core asset.
- **WebSockets / SSE replay on reconnect** — the frontend refetches state on connect; `event_id` dedup covers the rest.
- **A continuous domain reconciler** — decision 45 deliberately solved operational recovery (stale claims, provider circuits, truthful timelines) without reintroducing one; don't propose it again without new evidence the lease-driven model is insufficient.

---

*History note: the pre-refactor roadmap (Redis topology, task-manager/reconciler, PR workflows) is preserved verbatim at [docs/history/pre-refactor/roadmap.md](docs/history/pre-refactor/roadmap.md). It describes a system that no longer exists — read it as context, not as a plan. The nine-phase-machine-era roadmap this file replaces is recoverable from git history on this branch if needed for comparison.*
