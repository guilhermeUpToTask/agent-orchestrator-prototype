# Experiment pr49-lease-hardening-2026-07-23 — fix plan for PR #49 review findings

Source: coordinator code review of PR #49 (feat/parallel-execution) + uncommitted unfreeze #13
work, 2026-07-23. Findings C1–C3 + important improvements, see session review report.

Preconditions evidence (all 2026-07-23):
- Probes: codex `OK` exit 0 (8.6s); grok `OK` exit 0 (7.4s, grok-4.5-build-free) — both
  recorded via runtime_wrapper `run` in this experiment's events.jsonl.
- insights.md flags: codex 87% first-pass (90–100% at medium/high), 1 past bubblewrap
  write-failure (devcontainer since patched — codex authored yesterday's phases); grok 3/3
  verified but all LOW risk (Packet B is its first MEDIUM — watch closely); mimo/pi_free
  excluded (0 verified lifetime; pi_free disabled for a worktree-isolation breach).
- shared-abstractions hits: `_run_with_cas_retry`, `_cycle_merge_lock`, `validate_acyclic`/
  `ready_nodes` — cited as MUST REUSE in packets below.
- Concurrent-session hazard: a live interactive `codex` process (pts/1, started 12:01) exists.
  Branch tips are treated as movable; re-check `git status`/tip immediately before every
  commit and merge.

## Wave 0 — coordinator only (HUMAN GATE before push)

**Task 0: commit yesterday's uncommitted #13 work onto feat/parallel-execution.**
Not delegable: main-worktree git surgery over FROZEN-domain changes (standing user
preference: Fable coordinates high-blast-radius work). Contains the fix for review
finding C1, which blocks merge of the PR as pushed.

Commit split:
1. `feat(domain,execution): unfreeze #13 — per-goal blocks, symmetric goal leases, worker pool`
   — domain aggregate + navigation + dependency_graph + execution/planning handlers +
   use cases + worker pool + CLI flag + API surface + all tests (incl. untracked
   test_goal_blocks.py, test_worker_pool.py) + CLAUDE.md/ROADMAP/decision-log/ADR-001.
2. `fix(planning): route approved replan intent to architect_cycle before active-cycle branch`
   — planning_handler.py + advance_plan.py routing reorder + its regression test.
3. `fix(reasoner): wrap submission ValidationError as transient ReasonerUnavailable`
   — openai_reasoner.py + test_openai_reasoner.py additions.

Excluded riders (left uncommitted; user decides disposition): `.claude/settings.json`,
`.codex/skills/graphify/*`.
Gate: user approves split + rider disposition; then push and proceed to Wave 1.

## Wave 1 — 3 parallel writers (codex 1/1 + grok 1/1 + claude_sonnet 1/3 = 3 ≤ 4 global)

Concern signatures are disjoint: A = goal-lease lifecycle (infra/db + app worker loop),
B = git workspace crash recovery, C = replan use-case guard. No path overlap.

### Packet A → codex — goal-lease lifecycle enforcement (findings C2)
- Risk: HIGH (concurrency contract). Escalation: coordinator (chain high = [codex, claude]).
- Branch/worktree: `task/lease-lifecycle` (own worktree).
- Objective:
  1. `_HEARTBEAT_SQL` (src/infra/db/goal_lease_repository.py:35-44): renew only while
     unexpired — add `AND lease_expires_at >= :now_epoch`. Boundary pinned: claim-steal is
     `lease_expires_at < now`, so renewable iff `>= now` (no dead zone, no overlap).
  2. `GoalLeaseRepository.heartbeat` returns `bool` (rowcount==1); update the port
     (src/domain/repositories/goal_lease_repo.py), the SQLite repo, and the fake
     (src/app/testing/fakes.py — keep boundary semantics byte-identical to SQL).
  3. `drive_goal` (src/app/use_cases/run_worker.py:169-208): when a heartbeat returns False,
     finish the CURRENT atomic unit (never abort mid-unit — finalize's task-identity CAS is
     the safety net) but do NOT start another loop iteration; return a distinct
     `"lease_lost"` signal. Worker main logs it at INFO (structlog, e.g.
     `worker.goal_lease_lost`), not ERROR.
  4. Tests (dual-backend where applicable): heartbeat-after-expiry is refused (fake AND real
     SQLite); steal-then-driver-stops at drive_goal level using FakeClock.advance().
- MUST REUSE: `ExecutionHandler._run_with_cas_retry` shape if any retry is needed — do NOT
  hand-roll retry loops; `FakeClock` for time control; env_factory truth-test parametrization.
- Relevant paths: the five files above + tests/unit/orchestration/test_goal_lease_repository.py,
  tests/unit/orchestration/test_goal_parallel_execution.py.
- Prohibited: src/app/handlers/execution_handler.py, src/infra/git/workspace.py,
  src/domain/aggregates/**, src/infra/worker/main.py beyond the log line, alembic (no schema
  change), everything else.
- Verify (authoritative): `ruff check src tests`, `mypy src`, `pytest -m "not integration"`.
- Max attempts: 1 + 1 evidence-driven repair → escalate to coordinator.

### Packet B → grok — merge_goal crash-wedge self-heal (finding C3)
- Risk: MEDIUM (grok's cap; its first medium — one attempt, tight scope). Escalation: codex.
- Branch/worktree: `task/merge-wedge-prune` (own worktree).
- Objective: in `_merge_goal_sync` (src/infra/git/workspace.py:258-282), if the
  `git worktree add` of the throwaway cycle-merge worktree fails, run `git worktree prune`
  once and retry the add exactly once — INSIDE the already-held `_cycle_merge_lock`.
  Regression test in tests/integration/test_git_workspace.py: register a merge worktree,
  delete its directory (simulated crash), call `merge_goal`, assert it succeeds.
- MUST REUSE: `GitBranchWorkspace._cycle_merge_lock` (already wraps this window — do not add
  a second lock); the existing `_prune_sync` (workspace.py:146) — call it, don't reimplement.
- Prohibited: `_commit_sync`, any file other than the two named.
- Verify: `pytest tests/integration/test_git_workspace.py -m integration`, `ruff check src tests`,
  `mypy src`.
- Max attempts: 1 + 1 repair → escalate to codex.

### Packet C → claude_sonnet — request_replan resolution guard
- Risk: LOW. Branch/worktree: `task/replan-resolution-guard` (own worktree, model: sonnet).
- Objective: src/app/use_cases/request_replan.py:53 assumes `"start_replan"` is in every
  active goal-block's `legal_resolutions`. Pin behavior: if absent, raise `InvalidEditError`
  (existing domain error, stable code) naming the goal/block — fail loud, never skip
  silently. Add a unit test constructing a goal block without `start_replan` and asserting
  the error code; keep existing replan tests green.
- Prohibited: domain aggregate changes, other use cases.
- Verify: `ruff check src tests`, `mypy src`, `pytest -m "not integration"`.
- Bounded report: under 150 words.

## Wave 2 — after Wave 1 verified+merged (codex 1 + claude_sonnet 1 + coordinator = 3 ≤ 4)

### Packet D → codex — execution-handler preflight + contention logging
- Risk: HIGH. Branch: `task/handler-preflight`. Sequenced after A (same drive/worker seam).
- Objective: (1) `handle` txn1 preflight (src/app/handlers/execution_handler.py:~143): also
  return `Signal.PAUSED` when `plan.status == BLOCKED` or an active plan-wide scalar block
  exists — an in-flight goal driver must stop advancing beneath an operator-facing block;
  never auto-resolve. (2) In `_run_goal` (src/infra/worker/main.py), catch `StaleVersionError`
  distinctly and log INFO `worker.goal_claim_contention` (benign under symmetric leases)
  instead of ERROR `worker.goal_tick_failed`; all other exceptions unchanged.
- MUST REUSE: `_run_with_cas_retry` — extend only if the spec above proves insufficient;
  do not change txn boundaries.
- Verify: full `pytest`, ruff, mypy.

### Packet E → claude_sonnet — shareable fake goal-lease repo + cross-UoW test
- Risk: MEDIUM (sonnet cap OK). Branch: `task/fake-lease-sharing`. Depends on A's semantics.
- Objective: `InMemoryUnitOfWork` (src/app/testing/fakes.py:236) constructs a fresh
  `InMemoryGoalLeaseRepository` inline — make it injectable/shareable exactly like the plans
  repo, then add a two-UoW contention test (claim in one, steal-after-expiry in the other,
  heartbeat refusal for the loser) mirroring the real-SQLite two-thread test.
- Verify: `pytest -m "not integration"`, ruff, mypy.

### Coordinator (no delegation): status precedence + docs
- `status_reason`/`legal_actions`/`activity` currently mask `goal_blocks` behind a scalar
  block — surface partial blocks. FROZEN-domain presentation change → coordinator per
  standing user preference; assess whether it needs a decision-log note (presentation-only).
- Doc sync: known-issues/ADR touch-ups for anything fixed here; update PR #49 body.

## Wave 3 — coordinator integration & close-out
- Re-check branch tip before every merge (live codex session!). Merge verified branches,
  run the full authoritative gate: `ruff check src tests`, `mypy src`, `pytest` (all,
  including the SQLite truth-test parametrization), frontend `npm run build` if any API
  shape moved (none expected).
- Update `.orchestrator/shared-abstractions.md` (candidates: lease-liveness/heartbeat-refusal
  pattern; prune-then-retry-once recovery shape).
- `report.py pr49-lease-hardening-2026-07-23`, then `insights.py`; update manifest probe_result
  lines and grok risk-history note (first medium task outcome).

## Quota & reserve
- codex: quota unknown (manual/low confidence) — 2 packets total, sequential across waves,
  respects max_concurrent 1 and leaves headroom for repair duty (it is also Packet B's
  escalation target). Live interactive codex session shares the same account: expect
  contention, keep prompts lean.
- grok: free-tier daily quota, 1 packet + probe today; treat 429/exhaustion as escalate-to-codex.
- claude_sonnet: shares this session's budget — packets ship only the excerpts named above,
  bounded reports.
- Reserve: coordinator + codex repair lane preserved per 15% policy; mimo/pi_free not used.

## Open decisions for the user (block Wave 0)
1. Approve the Wave-0 three-commit split and push.
2. Rider disposition: `.claude/settings.json` and `.codex/skills/graphify/*` — hold
   uncommitted, or commit separately outside PR #49?
3. Confirm Packet C's pinned behavior (fail-loud `InvalidEditError`) and Packet D's
   "PAUSED beneath plan-wide block" semantics — both are small policy choices I've pinned
   but they are operator-visible.
