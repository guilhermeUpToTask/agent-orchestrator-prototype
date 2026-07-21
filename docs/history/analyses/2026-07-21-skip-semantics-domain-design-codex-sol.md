# Skip semantics + navigation/promotion (finding #3) — codex gpt-5.6-sol analysis

_Read-only design analysis by codex `gpt-5.6-sol` (high temperature), 2026-07-21. Answers 'does a skippable goal/task make sense?' → NO for the cyclic model. Proposed plan (un-freeze #11)._

## Analysis

`SKIPPED` was designed as an iteration-abandonment marker for the legacy execution loop, not as successful finite work. It belongs to the old model where replanning appends a replacement roadmap to the same root `goals` collection and the navigation scan must permanently pass over abandoned entries.

The lifecycle enum makes `SKIPPED` terminal alongside `DONE` and `FAILED` ([lifecycle.py:14](backend/src/domain/value_objects/lifecycle.py:14), [lifecycle.py:25](backend/src/domain/value_objects/lifecycle.py:25)). `Task.skip()` accepts only `PENDING`; `Task.abandon()` additionally accepts `RUNNING`; neither can be retried because `Task.retry()` accepts only `FAILED` ([task.py:73](backend/src/domain/entities/task.py:73), [task.py:77](backend/src/domain/entities/task.py:77), [task.py:81](backend/src/domain/entities/task.py:81)). `Goal.skip()` similarly records an abandoned iteration and permits a running goal to close only after every child task is terminal ([goal.py:40](backend/src/domain/entities/goal.py:40)).

The only production callers of `Task.skip()` and `Goal.skip()` are the legacy-root replan operations in `Plan.begin_replanning()` and `Plan.commit_replanned_goals()`:

- `begin_replanning()` skips pending root goals and pending tasks within running root goals ([planner_orchestrator.py:469](backend/src/domain/aggregates/planner_orchestrator.py:469), [planner_orchestrator.py:487](backend/src/domain/aggregates/planner_orchestrator.py:487)).
- `commit_replanned_goals()` abandons every remaining live task, skips its goal, appends replacement goals, and increments the legacy iteration ([planner_orchestrator.py:530](backend/src/domain/aggregates/planner_orchestrator.py:530)).
- `set_iteration_goals()` preserves every terminal root goal, including skipped history, while replacing the unfinished roadmap ([planner_orchestrator.py:513](backend/src/domain/aggregates/planner_orchestrator.py:513)).

That solves a real legacy-loop problem: without terminal abandonment markers, the append-only root-goal scan could resurrect work from the superseded iteration. The regression tests explicitly encode that rationale ([test_replan_loop.py:71](backend/tests/unit/orchestration/test_replan_loop.py:71), [test_replan_loop.py:159](backend/tests/unit/orchestration/test_replan_loop.py:159), [test_replan_loop.py:219](backend/tests/unit/orchestration/test_replan_loop.py:219)).

The cyclic model has a different abandonment boundary. Executable goals come from `active_cycle.goals`, not root `goals` ([planner_orchestrator.py:197](backend/src/domain/aggregates/planner_orchestrator.py:197)). Replacement activation atomically changes the old cycle to `SUPERSEDED` and installs the new active cycle ([planner_orchestrator.py:815](backend/src/domain/aggregates/planner_orchestrator.py:815)). Therefore no per-task terminal marker is required to keep the old cycle out of navigation: cycle status already supplies that boundary.

Nevertheless, `request_replan()` currently mutates every pending, running, or failed task in the still-active source cycle into `SKIPPED` ([request_replan.py:44](backend/src/app/use_cases/request_replan.py:44)). This creates three semantic problems:

1. The source cycle is not yet superseded; it remains the active cycle throughout conversational replanning.
2. A cancelled or otherwise abandoned replan cannot resume the source cycle because its tasks have been irreversibly skipped.
3. If worker execution is accidentally re-entered before supersession, the active goal contains terminal work that never satisfied its contract or evidence requirements.

The navigation/promotion divergence is exact. `next_action()` ignores all terminal tasks, reports failure only if one is `FAILED`, and otherwise returns `(goal, None)` ([navigation.py:36](backend/src/domain/services/navigation.py:36), [navigation.py:45](backend/src/domain/services/navigation.py:45), [navigation.py:56](backend/src/domain/services/navigation.py:56)). Thus `{DONE, SKIPPED}` is interpreted as “closeable.” `Plan.peek_next()` delegates without adding cyclic semantics ([planner_orchestrator.py:209](backend/src/domain/aggregates/planner_orchestrator.py:209)).

Cyclic promotion instead requires every task to be `DONE` and to contain verification evidence, both when reserving and after the external merge ([execution_handler.py:754](backend/src/app/handlers/execution_handler.py:754), [execution_handler.py:810](backend/src/app/handlers/execution_handler.py:810)). The handler catches the discrepancy and opens a block ([execution_handler.py:137](backend/src/app/handlers/execution_handler.py:137), [execution_handler.py:707](backend/src/app/handlers/execution_handler.py:707)), but a skipped task cannot take the advertised retry route and cannot become `DONE`. The hot loop is gone, but the state is still irreparable except by superseding the cycle.

Treating skipped tasks as successful under option (a) would weaken the central cyclic guarantee that goal branches reach the cycle branch only after every task is done with accepted revision-bound evidence ([known-issues.md:54](docs/architecture/known-issues.md:54)). A human decision not to perform contracted work is a contract change and should produce a replacement draft/cycle, not evidence-free promotion.

## Design plan

Choose direction **(c), reinforced by (b): retain `SKIPPED` solely as a legacy iteration-history state, remove it from active cyclic execution, and make cyclic navigation and promotion share one strict eligibility predicate.**

The invariant should be:

> For an active cycle, `(goal, promotion-ready)` is reachable if and only if every task is `DONE` with accepted, current-revision verification evidence. A `SKIPPED` task inside an active cycle is invalid abandoned-work state, never successful completion. Legacy navigation remains terminal/no-failure based.

### 1. Separate cyclic and legacy replanning

Split `Plan.begin_replanning()` at its existing `active_cycle is None` boundary ([planner_orchestrator.py:483](backend/src/domain/aggregates/planner_orchestrator.py:483)):

- Preserve the legacy branch byte-for-byte: phase guard, pending root-goal/task skipping, pause clearing, and legacy phase transition.
- In the cyclic branch, do not call `Task.skip()`, `Task.abandon()`, or `Goal.skip()` on either root goals or active-cycle goals. Retain the source cycle unchanged while the replacement proposal and draft are prepared.
- Explicitly set the compatibility `phase` to `REPLANNING` and authoritative `status` to `WAITING`, instead of relying on `_set_phase()` to mutate both authorities. Continue clearing stale planning slots as introduced by un-freeze #10 ([planner_orchestrator.py:498](backend/src/domain/aggregates/planner_orchestrator.py:498)).

Remove the cyclic task-abandonment loop from `request_replan()` ([request_replan.py:46](backend/src/app/use_cases/request_replan.py:46)). Replanning should revoke claimability through `PlanStatus.WAITING`; it should not rewrite the source cycle’s task outcomes.

Late worker results still need their execution ledger marked abandoned, but cyclic stale-result paths should not convert the domain task to `SKIPPED`. Review `ExecutionHandler._abandon_stale()` and the other `abandon_execution_task()` callers at [execution_handler.py:643](backend/src/app/handlers/execution_handler.py:643), [execution_handler.py:1122](backend/src/app/handlers/execution_handler.py:1122), and [execution_handler.py:1301](backend/src/app/handlers/execution_handler.py:1301). For cyclic work invalidated by replanning or supersession, close the execution attempt/run and emit abandonment telemetry without changing the historical task outcome. Keep `Plan.abandon_execution_task()` available for the legacy loop ([planner_orchestrator.py:179](backend/src/domain/aggregates/planner_orchestrator.py:179)).

`Task.skip()`, `Task.abandon()`, and `Goal.skip()` should remain for persisted legacy plans and legacy append-only history. Removing the enum or entity methods globally would be an unnecessary migration/API break. Their documentation should state that they are legacy iteration-abandonment transitions and are forbidden for active-cycle completion.

### 2. Introduce one canonical cyclic promotion predicate

Add a pure domain predicate near navigation, such as `can_promote_goal(goal)`, whose definition is exactly:

- every task has `status == Status.DONE`;
- every task has accepted verification evidence valid for that task and current revision;
- optionally centralize the evidence-validity detail in an accompanying diagnostic function so navigation can identify the first offending task.

Do not merely test for a non-empty evidence list if the existing evidence model exposes revision/acceptance validity; the canonical predicate should express the actual accepted-evidence rule.

Use this predicate in all three places:

- cyclic `Plan.peek_next()`;
- `_reserve_goal_promotion()`;
- the post-merge revalidation in `_promote_goal()`.

This removes the duplicated comprehensions currently at [execution_handler.py:762](backend/src/app/handlers/execution_handler.py:762) and [execution_handler.py:824](backend/src/app/handlers/execution_handler.py:824).

### 3. Give navigation an explicit invalid-completion result

Keep `next_action(goals, now)` unchanged for legacy callers. Its current treatment of skipped history is required for old root-goal scans.

For active cycles, have `Plan.peek_next()` invoke a strict cyclic navigation variant or pass an explicit promotion policy. When no runnable task remains:

- `FAILED` continues to yield `GOAL_FAILED`;
- canonical promotion eligibility yields the existing promotion-ready result;
- any other state—`SKIPPED`, evidence-less `DONE`, stale evidence—yields a distinct `GOAL_UNPROMOTABLE`/`INVALID_COMPLETION` result.

Update `NextAction` accordingly ([navigation.py:10](backend/src/domain/services/navigation.py:10)). `ExecutionHandler.handle()` should dispatch this result directly to the structured-block path rather than speculatively reserving promotion and catching `TaskFailed` ([execution_handler.py:137](backend/src/app/handlers/execution_handler.py:137)).

Keep reservation and post-merge checks despite navigation eligibility: they remain necessary transaction/concurrency guards. They should use the same predicate and retain their current failure behavior if state changes after selection.

For a persisted active cycle already containing `SKIPPED`, expose only `start_replan` as a truthful repair. Do not advertise retry or edit unless the corresponding transition can actually reopen that status. Evidence-less `DONE` should use an explicit redo/reopen resolution if supported; it should not masquerade as an editable pending task.

### 4. Preserve legacy behavior exactly

When `active_cycle is None`:

- `begin_replanning()` continues to skip abandoned root work.
- `commit_replanned_goals()` continues finalize-abandoning old iteration entries.
- `set_iteration_goals()` continues preserving terminal history.
- `next_action()` continues passing over `SKIPPED` tasks and permits a legacy goal with done/skipped, non-failed tasks to close.
- `_complete_legacy_goal()` remains unchanged ([execution_handler.py:698](backend/src/app/handlers/execution_handler.py:698)).

No persistence migration is required because `Status.SKIPPED` remains readable and meaningful for legacy and historical data.

### 5. Limit `PlanPhase` compatibility side effects

This change touches the issue-#41 dual-authority area because cyclic `begin_replanning()` currently calls `_set_phase()`, which writes both compatibility `phase` and authoritative `PlanStatus` ([planner_orchestrator.py:144](backend/src/domain/aggregates/planner_orchestrator.py:144)). In the cyclic branch, write `phase=REPLANNING` only as a compatibility projection and set `status=WAITING` explicitly. Do not broaden this work into removal of `PlanPhase`.

Cycle activation’s compatibility `_set_phase(ENRICHING)` call ([cyclic_planning.py:307](backend/src/app/use_cases/cyclic_planning.py:307)) is adjacent debt but need not change for finding #3 unless the implementation establishes a general projection-only helper. Avoid an incidental global refactor.

### 6. Test changes

Update or add the following guards:

- `test_aggregate_navigation.py`: retain the legacy skipped-task scan test at [test_aggregate_navigation.py:121](backend/tests/unit/orchestration/test_aggregate_navigation.py:121); add cyclic-policy tests for fully evidenced `DONE`, `SKIPPED`, evidence-less `DONE`, stale evidence, and `FAILED`.
- `test_execution_handler_unpromotable_goal.py`: change the regression from “reservation throws and is caught” to “strict navigation returns invalid completion and opens a block”; add a skipped-task case and assert only executable resolutions are advertised ([test_execution_handler_unpromotable_goal.py:40](backend/tests/unit/orchestration/test_execution_handler_unpromotable_goal.py:40)).
- `test_conversation_and_planning.py`: replace the assertion that cyclic replanning skips every active-cycle task ([test_conversation_and_planning.py:297](backend/tests/unit/orchestration/test_conversation_and_planning.py:297)) with assertions that task/goal states remain unchanged while the plan reaches the un-freeze-#10 `WAITING` tuple and planning slots are retired.
- `test_execution_safety.py`: replace the expected skipped task in the superseded-cycle late-result test ([test_execution_safety.py:289](backend/tests/unit/execution/test_execution_safety.py:289)) with ledger/outbox abandonment plus unchanged historical task state.
- `test_cyclic_project_plan.py`: extend replan activation coverage to prove the source cycle becomes `SUPERSEDED` atomically and its internal task statuses were not used as the supersession mechanism ([test_cyclic_project_plan.py:146](backend/tests/unit/orchestration/test_cyclic_project_plan.py:146)).
- Add cancellation/recovery coverage proving that abandoning a replacement draft leaves the active source cycle resumable rather than permanently skipped.
- Keep all legacy transition and replan-loop tests at [test_transitions.py:55](backend/tests/unit/orchestration/test_transitions.py:55) and [test_replan_loop.py:70](backend/tests/unit/orchestration/test_replan_loop.py:70) unchanged as the compatibility lock.

### 7. Un-freeze and composition

This requires a narrow domain un-freeze because it changes aggregate replan semantics, cyclic navigation results, and stale-execution settlement. Scope it to:

- cyclic `begin_replanning()` and `request_replan()`;
- canonical goal-promotion eligibility;
- cyclic navigation result typing;
- cyclic stale-result abandonment;
- associated orchestration/execution tests.

It composes directly with un-freezes #9 and #10. Un-freeze #9/#10 prevented repeated worker failure and established a coherent blocked/replanning root state; this change removes the underlying invalid cyclic task state and ensures navigation cannot again disagree with promotion. It preserves #10’s source-cycle retention and makes that retention meaningful: the source is frozen while replanning and is superseded only when replacement activation succeeds.