# ROADMAP

Everything designed or planned but **not yet implemented**, in priority order. Each item names its origin so you can read the full reasoning:

- **[EVO]** — the 2026-07-06 evolution plan ([archived](docs/history/planning/2026-07-06-orchestrator-evolution-plan-fable-5.md)), produced by a full-codebase archaeology with `file:line` evidence.
- **[MRF]** — the master roadmap the integration was executed against ([archived](docs/history/planning/2026-07-02-master-roadmap-final-fable-5.md)). Phases 0–2 and slices of 3–4 are **done**; the leftovers live here.
- **[LEG]** — pre-refactor features shelved with designed seams ([docs/legacy/pre-refactor-backend.md](docs/legacy/pre-refactor-backend.md)).

Verified defects backing the Now items are documented in [docs/architecture/known-issues.md](docs/architecture/known-issues.md).

---

## Now — safety hotfixes [EVO Phase 1]

Small, independent PRs; no schema changes; each revertible on its own.

### 1. 🔥 H1 — Close the double-execution window
The default worker lease (300s) is *shorter* than the default task timeout (600s), and heartbeats only happen between units — never during an agent run. With ≥2 workers (or a restart racing a live worker), a task running longer than the lease is claimed and executed a **second time, concurrently**.

- Spawn an asyncio heartbeat task around `drive_plan` (`backend/src/app/use_cases/run_worker.py`), cancelled in `finally`. Safe: the lease trio runs on its own short sessions, outside the UoW.
- Boot check in `backend/src/infra/worker/main.py`: warn + clamp the effective lease to `agent_runner.timeout_seconds + 60` when smaller.
- Unify the inconsistent lease defaults (60 in `worker_tick`, 300 elsewhere) into one constant.
- **Done when:** a 700s scripted task under a 300s lease with two workers executes exactly once, on both truth-test backends.

### 2. 🔥 H2 — Poisoned-plan starvation
A plan whose drive raises before any save keeps `updated_at` unchanged; the claim query (`ORDER BY updated_at`) picks it **first, every poll tick, forever** — starving every other plan on a single-worker deployment.

- `release()` in `backend/src/infra/db/plan_repository.py` sets `updated_at = now`, rotating failing plans to the back of the claim queue.
- **Done when:** poisoned plan + healthy plan ⇒ the healthy plan advances within two ticks (truth test).

## Next — observability, the 3am fixes [EVO Phase 2]

The operator currently cannot answer "what happened to attempt 2 of task X" without reading worker stdout.

3. **Attempt history on the task** — `requeue()` currently *erases* the failed attempt's result; keep a bounded `attempt_history` instead. Requires a deliberate one-field domain un-freeze (precedent: the AgentSpec runtime fields, 2026-07-05). Alembic revision is additive.
4. **Stop replaying agent history on API boot** — the relay's `agent_events` cursor restarts at 0 every startup, re-publishing the whole table to SSE. Start at `MAX(id)` or persist the cursor in `config`.
5. **Retention** — `outbox`, `agent_events`, and `plan_chat_messages` grow forever. Add a `db prune` CLI command (delivered outbox rows, aged agent events).
6. **Attempt-history endpoint** — `GET /api/plans/{id}/tasks/{tid}/attempts` so incidents are answerable from the API alone.

## Then — hygiene, deletion over addition [EVO Phase 3]

7. Delete `LocalDirWorkspace` (unused), the `publish_sse` shim (confirm no importers first), and migrate tests off the functional `advance_plan` wrapper.
8. `git worktree prune` in the workspace `begin` path + a `workspace gc` CLI for dead `plan/*` branches — crashes currently leak worktrees and branches forever.
9. Target: ≥150 lines deleted; `git worktree list` stable across a kill-mid-task chaos run.

## Later — evidence-gated capability work

Take these up only when real usage demonstrates the need.

10. **Multi-worker deployment, documented + truth-tested** [EVO Phase 4] — the code supports it once H1 lands; add a two-worker truth test and operator docs.
11. **Goal-level parallelism** [MRF 0.2, ADR-001] — the lease *granularity* is the designed parallelism switch (plan → goal → task). Requires `next_action` returning a set of ready units and a workspace merge-conflict strategy. The `Goal.depends_on` DAG seam already exists, unused. Do **not** bolt a queue on top; move the lease.
12. **Mutation guards `PLAN_BUSY` / `TASK_RUNNING`** [MRF 3.5] — task/goal edit-delete guards beyond the current status checks; plan-DELETE gated by the lease. The HTTP codes are already reserved in the API error map.
13. **`manual_retry` use case** [MRF, decision #11] — human-triggered retry that clears the backoff gate, resets attempts, and requeues, bypassing `should_retry`.
14. **Worker/scheduler health surface** [MRF 3.6] — expose last-heartbeat, current claims, and restart counts (a `/api/workers` endpoint). The lease is the recovery *mechanism*; this is *visibility* — you can't tell a hung worker from an idle one today.
15. **Launcher / OS supervision** [MRF 3.2] — a thin, idempotent supervisor (systemd or process manager) that restarts a dead worker; the lease handles the takeover. Document the failure modes; no distributed consensus.
16. **pi NDJSON streaming** [MRF 2.4] — the full pi stdio handshake streaming fine-grained agent events; the seam is `src/infra/runtime/pi_protocol.py` (agent events currently emit only start/finish, `seq` 0/1).
17. **Redis claim path** [MRF 3.1] — swap the SQLite lease transport behind the repository port *only if* multi-machine workers become real. The SQLite lease is deliberately sufficient for local-first.
18. **CI pipeline** [MRF 5] — per-PR: unit + integration + dummy e2e + ruff/mypy; nightly/merge-only: the paid real-model smoke. The split matters — don't burn money per push.
19. **Frontend E2E (Playwright)** [MRF 5, [archived plan](docs/history/planning/2026-06-15-playwright-e2e-plan-deferred.md)] — one full-cycle browser walk against the dry-run stack. The archived plan targets the *old* API and needs rewriting against `/api/plans/{id}/…`; its environment lessons (webServer boot, sandbox SIGTERM, poll-don't-race-SSE) still apply.
20. **Unified telemetry store** [MRF 4.4] — one queryable persistence for outbox + agent_events + API request logs. Build on the existing two streams; **no second event system**.

## Deferred features — shelved with designed seams [LEG]

Documented in full, with reintroduction designs, in [docs/legacy/pre-refactor-backend.md](docs/legacy/pre-refactor-backend.md):

| Feature | Seam that preserves it |
|---|---|
| GitHub PR gate (orchestrator opens PRs, humans merge) | The `Workspace` port — a PR output strategy plugs in beside branch-merge |
| Project spec governance (`propose → diff → apply`) | Two-tier config + the `projects` table |
| Decision gate / decision history | Genuine whitespace — design preserved in the legacy doc |
| Env provisioner (uv/Bun) + framework questionnaire | Config fields first, provisioning later [MRF 2.8] |
| Autonomous ARCHITECTURE structuring pass | `PlanningHandler._architect` is an explicit passthrough seam |
| Repository indexing / symbol graph / context packaging | Never built; idea preserved |
| Replay & audit tooling (reconstruct a run from events) | Outbox + agent_events already carry the data |

## Do-not-do list [EVO]

Tempting improvements explicitly rejected — with reasons — so they aren't re-litigated by default:

- **Temporal / DBOS / Celery / Redis now** — this is a local-first tool whose deployment story is one SQLite file; a workflow engine adds a server dependency and makes the human-gated phase machine *harder* to test. Re-evaluate only if a multi-executor pool starts re-inventing workflow versioning.
- **Task-level parallelism now** — no throughput evidence; it breaks the plan-document CAS model for a speculative gain.
- **Splitting the plan JSON into relational goal/task rows** — the single document + the dual-backend truth tests are the system's core asset.
- **WebSockets / SSE replay on reconnect** — the frontend refetches state on connect; `event_id` dedup covers the rest.

---

*History note: the pre-refactor roadmap (Redis topology, task-manager/reconciler, PR workflows) is preserved verbatim at [docs/history/pre-refactor/roadmap.md](docs/history/pre-refactor/roadmap.md). It describes a system that no longer exists — read it as context, not as a plan.*
