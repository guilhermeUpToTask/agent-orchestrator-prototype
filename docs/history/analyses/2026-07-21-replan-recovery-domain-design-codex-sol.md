# Replan-recovery domain design (findings #12/#13) — codex gpt-5.6-sol analysis

_Read-only design analysis by codex `gpt-5.6-sol` (high temperature), 2026-07-21, for the frozen-domain fix of the cyclic replan-recovery state. Proposed plan; NOT yet implemented — awaiting decision._

## Design analysis

ADR-003 makes `PlanStatus` the root lifecycle authority: `running` is worker-claimable, `paused` means a settled human pause, and `waiting` means conversational planning or human review is required ([plan-lifecycle.md:22](docs/architecture/plan-lifecycle.md:22)). Its migration table explicitly maps a bound legacy `discovery`/`replanning` plan to `WAITING` ([adr-003-cyclic-project-plan-lifecycle.md:55](docs/decisions/adr-003-cyclic-project-plan-lifecycle.md:55)).

Finding #12 is therefore an invalid state, not a legitimate kind of pause:

```text
status=PAUSED
paused=False
phase=REPLANNING
active_cycle=<active>
```

`legal_actions` advertises `resume` solely from `status == PAUSED` ([planner_orchestrator.py:595](backend/src/domain/aggregates/planner_orchestrator.py:595)), while `resume()` authorizes the transition solely from `paused` ([planner_orchestrator.py:302](backend/src/domain/aggregates/planner_orchestrator.py:302)). The SQL worker predicate independently requires `status='running'`, `paused=0`, and `pause_requested=0` ([plan_repository.py:42](backend/src/infra/db/plan_repository.py:42)). Thus neither the API nor worker can advance the plan.

The immediate cause is ordering. `begin_replanning()` calls `_set_phase(REPLANNING)`, temporarily mapping the status to `WAITING` ([planner_orchestrator.py:469](backend/src/domain/aggregates/planner_orchestrator.py:469)), but `request_replan()` subsequently resolves the block ([request_replan.py:22](backend/src/app/use_cases/request_replan.py:22)). Generic `resolve_block()` then overwrites the status with `PAUSED` merely because an active cycle exists, without setting `paused=True` ([planner_orchestrator.py:833](backend/src/domain/aggregates/planner_orchestrator.py:833)).

Finding #13 is the artifact counterpart. `begin_replanning()` clears the manual pause and changes the legacy phase, but does not retire the current `intent_proposal`, `cycle_draft`, or `review_gate` slots ([planner_orchestrator.py:469](backend/src/domain/aggregates/planner_orchestrator.py:469)). An approved proposal is particularly harmful:

- `activity` derives `cycle_architecture` from an approved proposal with no draft ([planner_orchestrator.py:621](backend/src/domain/aggregates/planner_orchestrator.py:621)).
- `propose_intent()` rejects every non-cancelled existing proposal, including an approved one ([planner_orchestrator.py:683](backend/src/domain/aggregates/planner_orchestrator.py:683)).
- The conversation guard now correctly permits an approved prior intent, but the eventual commit still calls `fresh.propose_intent(...)` and therefore fails ([conversation.py:83](backend/src/app/use_cases/conversation.py:83), [conversation.py:271](backend/src/app/use_cases/conversation.py:271)).

### Required conversational-replan invariant

Before the reasoner has produced a candidate, a cyclic plan in conversational replan should hold:

```text
status             = WAITING
paused             = False
pause_requested    = False
phase              = REPLANNING       # compatibility projection only
active_cycle       = existing source cycle
intent_proposal    = None             # active planning slot is empty
cycle_draft        = None
review_gate        = None
block              = resolved/inactive
activity           = replan_discovery
worker claimable   = false
legal action       = continue/start replan conversation, never resume
```

Once the reasoner produces a candidate, the tuple becomes:

```text
status             = WAITING
paused             = False
phase              = REPLANNING
active_cycle       = unchanged source cycle
intent_proposal    = fresh, unapproved ProposalKind.REPLAN
review_gate        = unresolved INTENT gate for that exact revision
activity           = review:intent
```

`WAITING` is the correct status. Conversational discovery is not autonomous worker work, so `RUNNING` would cause the repository to claim it. `PAUSED` is also wrong because ADR-003 reserves that status for a settled manual pause.

`begin_replanning()` should not fabricate an empty `IntentProposal`: the entity requires an objective and other reasoner-derived content ([planning_artifacts.py:99](backend/src/domain/entities/planning_artifacts.py:99)). It should open the *slot* for a fresh proposal by clearing stale current-artifact pointers. The conversation use case should continue constructing the actual `ProposalKind.REPLAN`, binding it to `active_cycle.id`, and opening its exact-revision gate when a candidate exists ([conversation.py:271](backend/src/app/use_cases/conversation.py:271)).

Approved source intent history remains represented by the active cycle’s `intent_proposal_id`; cyclic activation already clears the aggregate’s current proposal/draft/gate slots ([planner_orchestrator.py:783](backend/src/domain/aggregates/planner_orchestrator.py:783)). Clearing stale current pointers during replan is therefore consistent with existing lifecycle semantics rather than destroying the source cycle.

## Legacy `PlanPhase` side-effect audit

The following are active legacy dependencies that can diverge from cyclic authority.

1. `_set_phase()` mutates both the compatibility projection and authoritative root status ([planner_orchestrator.py:144](backend/src/domain/aggregates/planner_orchestrator.py:144)). This is the central dual-authority hazard. Any cyclic compatibility update can silently overwrite a carefully selected status. It happens in `begin_replanning`, `retry_agent_binding`, planning enrichment, and activation.

2. `activate_cycle()` first establishes cyclic state and `RUNNING`, after which the application calls `_set_phase(ENRICHING)` ([cyclic_planning.py:277](backend/src/app/use_cases/cyclic_planning.py:277)). Today both happen to map to `RUNNING`, but a compatibility write still has authority over status. A future phase projection change could make a freshly activated cycle non-claimable.

3. Cyclic enrichment calls `_set_phase(RUNNING)` after enriching a goal ([planning_handler.py:322](backend/src/app/handlers/planning_handler.py:322)). It currently restores `RUNNING`, but can overwrite a status selected from cyclic artifacts or a concurrent lifecycle transition if the surrounding revalidation misses it.

4. `retry_agent_binding()` calls `_set_phase(RUNNING)` ([planner_orchestrator.py:373](backend/src/domain/aggregates/planner_orchestrator.py:373)). For a cyclic block, this phase write also changes root status, instead of the block-resolution command explicitly selecting the cyclic continuation.

5. `_assert_not_terminal()` reads `phase` and can declare the cyclic root terminal even though ADR-003 says the root is never terminal ([planner_orchestrator.py:162](backend/src/domain/aggregates/planner_orchestrator.py:162)). A cyclic row retaining `DONE` or `FAILED` as a stale projection can reject task finalization and recovery mutations regardless of active cycle/status.

6. Pause guards retain `WORKER_CLAIMABLE_PHASES` for legacy plans ([planner_orchestrator.py:264](backend/src/domain/aggregates/planner_orchestrator.py:264), [planner_orchestrator.py:286](backend/src/domain/aggregates/planner_orchestrator.py:286)). The active-cycle exception added by unfreeze #9 protects cyclic plans, but this remains fragile: any cyclic state temporarily lacking an active cycle, such as approved-intent architecture, falls back to phase authority even though it can be worker-claimable from status/artifacts.

7. `resume()` correctly recognizes an active cycle, but computes `RUNNING` whenever one exists, independent of gates, blocks, or a conversational replan ([planner_orchestrator.py:302](backend/src/domain/aggregates/planner_orchestrator.py:302)). If an inconsistent `paused=True` leaks into a waiting cyclic state, resume can bypass the artifact-driven wait and make it claimable.

8. `begin_replanning()` still sets root status indirectly through `_set_phase(REPLANNING)` ([planner_orchestrator.py:469](backend/src/domain/aggregates/planner_orchestrator.py:469)). Finding #12 demonstrates why relying on that incidental mapping is unsafe.

9. `commit_replanned_goals()` is guarded and advanced entirely by `PlanPhase`, writes legacy root `goals`, increments legacy `iteration`, and calls `_set_phase(ARCHITECTURE)` ([planner_orchestrator.py:517](backend/src/domain/aggregates/planner_orchestrator.py:517)). It is valid only for the legacy loop. Calling it from the cyclic conversation path would bypass `IntentProposal → ReviewGate → CycleDraft → ReviewGate → Cycle` and make the plan prematurely worker-claimable.

10. The cyclic planning handler has phase-based fallback branches after its artifact-based branches ([planning_handler.py:151](backend/src/app/handlers/planning_handler.py:151)). A malformed cyclic plan without the expected active artifact can fall through and be routed according to stale phase rather than stopping on an inconsistent cyclic state.

11. Execution candidate validation and tolerant finalization require both `phase == RUNNING` and `status == RUNNING` ([execution_handler.py:539](backend/src/app/handlers/execution_handler.py:539), [execution_handler.py:569](backend/src/app/handlers/execution_handler.py:569), [execution_handler.py:1053](backend/src/app/handlers/execution_handler.py:1053)). A cyclic plan that is legitimately running but retains `REPLANNING` as its projection can have valid results treated as stale/abandoned. Conversely, phase changes can force tolerant abandonment even if cyclic status/artifacts still authorize the run.

12. `PlanDispatcher` captures `phase` and uses it for all non-active-cycle fallback routing and terminal decisions ([advance_plan.py:64](backend/src/app/use_cases/advance_plan.py:64)). Active cycles are routed cyclically first, which limits current exposure, but approved-intent architecture and malformed/intermediate cyclic states can still fall into legacy routing.

13. Conversation response DTOs continue returning `plan.phase` ([conversation.py:213](backend/src/app/use_cases/conversation.py:213), [conversation.py:263](backend/src/app/use_cases/conversation.py:263), [conversation.py:342](backend/src/app/use_cases/conversation.py:342)). This is compatibility-only presentation, but clients can mistakenly treat it as current authority and reconstruct the retired state machine.

14. The repository persists both `status` and `phase` as independently queryable columns ([plan_repository.py:19](backend/src/infra/db/plan_repository.py:19)). Claiming correctly uses only status and pause fields, but every `_set_phase()` side effect is copied into the authoritative status column. Divergence between JSON, columns, or phase/status update paths directly affects worker eligibility.

## Minimal, safe design plan

1. Deliberately record a narrow domain unfreeze for the cyclic replan consistency invariant. Scope it only to `begin_replanning`, `request_replan`, derived activity/actions, and regression tests. Do not use this fix as an opportunity to remove `PlanPhase` globally.

2. Split `Plan.begin_replanning()` behavior by lifecycle family at [planner_orchestrator.py:469](backend/src/domain/aggregates/planner_orchestrator.py:469):

   - Preserve the existing legacy branch byte-for-byte when `active_cycle is None`: retain the `{RUNNING, REVIEW}` phase guard, legacy root-goal skipping, pause clearing, and `_set_phase(REPLANNING)`.
   - For `active_cycle is not None`, make the cyclic postcondition explicit: clear manual pause fields and `pause_requested`; set the compatibility `phase` to `REPLANNING`; set `status=WAITING` directly; clear the current `intent_proposal`, `cycle_draft`, and `review_gate` slots; retain the active source cycle.
   - Do not create an `IntentProposal` here. The method has no candidate content and must not fabricate review history.
   - Either avoid applying the legacy `self.goals` abandonment loop to cyclic plans or document it as compatibility-only; cyclic task settlement belongs to the active cycle and is already handled by `request_replan()`.

   Risk: clearing stale artifact pointers changes observable plan detail, but only on an explicit cyclic replan. It is required to prevent approved intent/draft state from masquerading as current planning work.

3. Recompose `request_replan()` at [request_replan.py:22](backend/src/app/use_cases/request_replan.py:22):

   - Capture the legacy `from_phase`.
   - If an active block exists, validate and resolve `start_replan` first and emit `BlockResolved`.
   - Invoke `begin_replanning()` after block resolution so its explicit `WAITING` postcondition is the transaction’s final lifecycle decision.
   - Settle failed/pending/running tasks in the active cycle as today.
   - Bump once, emit `ReplanRequested`, and save once.

   This ordering prevents generic block resolution from overwriting the replan state. The transaction remains atomic, so no intermediate `PAUSED` state is externally visible.

4. Do not broaden `resolve_block()` in this patch. Its generic active-cycle fallback at [planner_orchestrator.py:833](backend/src/domain/aggregates/planner_orchestrator.py:833) is questionable because it can produce `PAUSED + paused=False`, but `request_replan` is its only current caller. A global semantic change would have unnecessary domain blast radius. Record the broader invariant violation as compatibility debt or a separately decision-logged cleanup.

5. Align pause action advertisement with the pause transition guard at [planner_orchestrator.py:595](backend/src/domain/aggregates/planner_orchestrator.py:595):

   - Preserve `resume()` as a manual-pause operation keyed on `paused`; it must not become a generic transition from any `status=PAUSED`.
   - Advertise `resume` only when the manual pause flag is actually set, ideally requiring both `status == PAUSED` and `paused`.
   - Treat `status == PAUSED && paused == False` as inconsistent state, not as resumable state.
   - Preserve legacy resume behavior for genuinely paused non-cyclic plans.

6. Extend derived cyclic presentation:

   - In `activity` at [planner_orchestrator.py:621](backend/src/domain/aggregates/planner_orchestrator.py:621), recognize `status=WAITING`, active cycle, `phase=REPLANNING`, and no current planning artifacts as `replan_discovery` before falling through to active-cycle execution activity.
   - In `legal_actions`, do not expose `resume` for this tuple. Expose the existing replan-conversation entry action, preferably `start_replan`; avoid returning `start_intent`, which implies initial planning. If API action naming cannot change in this frozen patch, returning no command while the dedicated replan-message endpoint remains usable is safer than advertising the wrong transition.

7. Keep cyclic conversation proposal creation where it is. `_conversation_turn()` should continue creating a fresh `ProposalKind.REPLAN` from the reasoner candidate, setting `source_cycle_id` from the still-active source cycle, and opening a new intent review gate atomically ([conversation.py:271](backend/src/app/use_cases/conversation.py:271)). No call to legacy `commit_replanned_goals()` should be introduced.

8. Update the truth tests:

   - Change `test_replan_from_blocked_cycle_settles_work_before_message` to expect `WAITING`, `paused=False`, resolved block, cleared stale proposal/draft/gate, non-claimability, no `resume`, and successful subsequent proposal commit ([test_conversation_and_planning.py:260](backend/tests/unit/orchestration/test_conversation_and_planning.py:260)).
   - Extend `test_cyclic_plan_can_begin_replanning_with_replanning_legacy_phase` with the complete tuple invariant and stale approved-intent fixture ([test_cyclic_pause_replan_phase_guards.py:39](backend/tests/unit/orchestration/test_cyclic_pause_replan_phase_guards.py:39)).
   - Retain the legacy rejection tests at [test_cyclic_pause_replan_phase_guards.py:47](backend/tests/unit/orchestration/test_cyclic_pause_replan_phase_guards.py:47) and legacy replan-loop tests at [test_replan_loop.py:70](backend/tests/unit/orchestration/test_replan_loop.py:70) to prove the `active_cycle is None` path remains unchanged.
   - Add fake/SQLite parity coverage beside the existing claim truth tests at [test_pause_resume.py:74](backend/tests/unit/orchestration/test_pause_resume.py:74): conversational replan is not claimable because it is `WAITING`, and becomes claimable only after intent approval advances it to `RUNNING`.
   - Add an API assertion around the existing replan walkthrough coverage that plan detail reports `WAITING`, `replan_discovery`, no `resume`, and accepts the next replan conversation turn.

The smallest safe correction is therefore: make cyclic `begin_replanning()` explicitly establish the `WAITING` conversational tuple, resolve a block before establishing that tuple, clear stale current planning artifacts without fabricating a proposal, and ensure the derived UI contract never advertises manual resume unless the manual pause flag is truly armed.