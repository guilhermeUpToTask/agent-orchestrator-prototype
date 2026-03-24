# Deep Code Review (Correctness, Architecture, Maintainability)

Date: 2026-03-24
Scope: `src/domain`, `src/app`, `src/infra`, orchestration/event paths

## Findings

### F1 (P0) — Redis event consumption is effectively **at-most-once**, not recoverable
- `RedisEventAdapter.subscribe_many()` ACKs each stream message in a `finally` block immediately after yielding the deserialized event.
- This means messages are acknowledged even when downstream handlers crash or partially fail.
- The comment says recovery is handled by reconciler, but reconciler cannot reconstruct every lost semantic transition (e.g., workflow-level events unrelated to task lease checks).
- **Impact:** non-reproducible orchestration, hidden event loss, hard-to-debug partial workflows.

### F2 (P0) — PR creation flow claims idempotency but can still create duplicate PRs
- `CreateGoalPRUseCase.execute()` always calls `github.create_pr(...)` when goal is `READY_FOR_REVIEW`.
- There is no pre-check for an existing open PR by branch/head before creating a new one.
- If `goal.ready_for_review` is replayed, or concurrent orchestrators run, duplicate PRs can be created upstream.
- **Impact:** external side effects are non-idempotent; recovery/retry can worsen state.

### F3 (P0) — Domain invariant hole: `TaskAggregate.cancel()` bypasses transition guards
- Unlike other transitions, `cancel()` does not assert allowed source statuses.
- It can cancel a task from any status (including terminal success/merged states), causing invalid histories.
- **Impact:** aggregate lifecycle can be corrupted by callers, weakening correctness guarantees.

### F4 (P1) — Application layer leaks infrastructure in spec proposal use case
- `ProposeSpecChange` imports `AtomicFileWriter`, `FileProjectSpecRepository`, and `infra.config` directly.
- This breaks hexagonal boundaries: application use case decides infrastructure details and filesystem fallback paths.
- **Impact:** difficult testing/substitution, architectural drift toward infra-coupled app layer.

### F5 (P1) — Task unblock logic is inconsistent for `REQUEUED` dependents
- `TaskUnblockUseCase` checks `task.is_ready_for_dispatch(...)`, which only returns true for `CREATED` tasks.
- A dependent task in `REQUEUED` with all dependencies satisfied is skipped here and relies on other pathways to be re-emitted.
- **Impact:** brittle sequencing and non-deterministic latency in dependency-driven execution.

### F6 (P1) — Reconciler phase completion transition lacks optimistic concurrency discipline
- `AdvanceGoalFromPRUseCase._check_phase_completion()` loads and saves plan directly (`self._plan_repo.save(plan)`) after computing completion from a snapshot.
- No versioned write / CAS semantics are used here unlike task/goal mutation hotspots.
- **Impact:** race windows in multi-process reconciler/CLI operation can overwrite plan transitions.

### F7 (P1) — Orchestration unlock signal is advisory-only and disconnected from task dispatch
- Orchestrator emits `goal.unlock_next` on `goal.approved` / `goal.merged` but does not directly trigger deterministic next-goal start.
- Actual start behavior is split across separate use cases and optional hooks.
- **Impact:** coordination is spread across events plus implicit consumers, increasing hidden coupling and drift risk.

### F8 (P2) — Duplicate handler methods and repeated routing patterns
- `TaskManagerHandler.handle_task_created` and `.handle_task_requeued` are effectively duplicate logic.
- Event-routing code in CLI/system command loops is hand-written with repeated branching and no central event map.
- **Impact:** increased maintenance surface and inconsistent future behavior when one branch is changed.

### F9 (P2) — In-memory event adapter semantics diverge from Redis adapter
- In-memory adapter is non-blocking and replays from local lists; Redis adapter is streaming consumer-group based.
- This mismatch can hide ordering/retry/ack issues in tests.
- **Impact:** tests may overestimate determinism and under-detect production eventing bugs.

---

## Violations (DDD / Hexagonal)

1. **Application → Infrastructure coupling** in `src/app/usecases/propose_spec_change.py`.
2. **Domain event contract drift**: event payload guidance says IDs-only, but multiple producers include operational fields (`reason`, `commit_sha`, etc.), creating blurry domain/application boundary.
3. **State transition protection inconsistency** in `TaskAggregate.cancel()` undermines aggregate-centric invariants.

---

## Orchestration Risks

1. **Hidden state loss from early ACK** (F1): event appears processed but business action may not have completed.
2. **Non-reproducible external effects** (F2): retries can create duplicate GitHub PR artifacts.
3. **Brittle sequencing** (F5, F7): next steps rely on distributed implicit behavior, not a single deterministic orchestrator state machine.
4. **Race-prone progression** (F6): plan-phase transitions lack compare-and-swap safeguards unlike task/goal flows.

---

## Refactor Plan (Prioritized)

### P0 — Critical bugs / architectural violations

#### Step P0.1 — Fix event ACK strategy in Redis adapter
- **Problem:** ACK occurs regardless of downstream success.
- **Proposed change:** move ACK responsibility to consumer after successful handler completion; introduce explicit nack/retry/dead-letter policy.
- **Expected benefit:** at-least-once processing with observable retries; fewer silent losses.
- **Risk level:** High (touches core event loop behavior).

#### Step P0.2 — Make PR creation truly idempotent
- **Problem:** `create_pr` can duplicate remote PRs.
- **Proposed change:** add `GitHubPort.find_open_pr(head, base)` and short-circuit to existing PR before create.
- **Expected benefit:** safe retries and deterministic external effects.
- **Risk level:** Medium.

#### Step P0.3 — Harden task cancellation transitions
- **Problem:** `TaskAggregate.cancel()` has no source-status guard.
- **Proposed change:** enforce allowed source states (e.g., FAILED/ASSIGNED/IN_PROGRESS/REQUEUED only) and test forbidden paths.
- **Expected benefit:** stronger domain invariants and auditability.
- **Risk level:** Low.

### P1 — Structural improvements

#### Step P1.1 — Remove infra imports from `ProposeSpecChange`
- **Problem:** app use case depends on concrete filesystem/config classes.
- **Proposed change:** add a dedicated `SpecProposalRepositoryPort` (or extend `ProjectSpecRepository`) that owns proposal path + atomic write.
- **Expected benefit:** restored hexagonal boundary; easier test doubles.
- **Risk level:** Medium.

#### Step P1.2 — Normalize dependency-unblock semantics
- **Problem:** REQUEUED dependents can be skipped in unblock flow.
- **Proposed change:** update domain query (`is_ready_for_dispatch`) to include assignable statuses or have unblock call `is_assignable + is_unblocked` directly.
- **Expected benefit:** deterministic dependency progression.
- **Risk level:** Low.

#### Step P1.3 — Add CAS/versioned save for project-plan transitions
- **Problem:** plan progression writes are last-write-wins.
- **Proposed change:** introduce `update_if_version` semantics for project plan repository and migrate `_check_phase_completion` to retry loop.
- **Expected benefit:** race-safe phase transitions.
- **Risk level:** Medium.

### P2 — Cleanup / optimization

#### Step P2.1 — Consolidate event routing maps
- **Problem:** repetitive `if/elif` routing logic across CLI daemons/handlers.
- **Proposed change:** centralize event-type → callable maps with shared loop utility.
- **Expected benefit:** lower duplication and easier extension.
- **Risk level:** Low.

#### Step P2.2 — Improve test realism for eventing
- **Problem:** in-memory adapter diverges from Redis semantics.
- **Proposed change:** add contract tests against Redis adapter behavior (ack/retry/order), and optionally a stricter in-memory test adapter that simulates ack lifecycle.
- **Expected benefit:** earlier detection of production-only failures.
- **Risk level:** Medium.

#### Step P2.3 — Document orchestration authority boundaries
- **Problem:** unlock/start responsibilities are split and partially advisory.
- **Proposed change:** define a single orchestration authority matrix (which component transitions which state) and enforce via tests.
- **Expected benefit:** reduced architectural drift and easier debugging.
- **Risk level:** Low.
