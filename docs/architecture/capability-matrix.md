# Capability-to-product coverage matrix

**What this is.** One authoritative answer to "which operator workflows does this
system support, and where is each capability exposed?" — every domain capability
traced through its application entry point, its FastAPI route, its frontend
consumer, and its tests, then classified and given a launch priority.

Produced by roadmap **Phase 3**, audited against the code at commit `f4b966e`
(2026-07-28). Every row was verified by reading the code, not inferred from
docs.

**How it stays honest.** `backend/tests/unit/test_capability_matrix.py` fails
when the FastAPI app serves an operation this file does not list, or when this
file names an operation the app does not serve. A new endpoint that skips the
matrix breaks the build. It cannot verify the *frontend* and *tests* columns —
those are re-audited when a phase closes.

## Legend

| Status | Meaning |
|---|---|
| `full` | Implemented, routed, consumed by the frontend, and tested |
| `api-only` | Implemented and routed with tests, but no frontend consumer |
| `ui-partial` | Routed and consumed, but the UI shows less than the API serves |
| `hidden` | Implemented in domain/app, reachable by no route |
| `untested` | Routed, but no test exercises the route |
| `legacy` | Nine-phase compatibility surface — **not** the live model |

Launch priority is `critical` (a walkthrough operator job needs it before the
peer preview), `post-launch`, or `compat`. No row is `critical` merely for
symmetry: each one names the operator job it serves.

## Operator jobs

The launch-critical column is anchored to the jobs in the walkthrough fixtures
(`fixtures/happy-path-v1/README.md` and `-v2`, `parallel-goals-v1`,
`planning-recovery-v1`, `contract-repair-v1`) — the only definition of "supported
workflow" this project has that was actually executed end to end.

| Job | What the operator does | Fixture step |
|---|---|---|
| **J1 Install** | migrate, seed the catalog, pick a tier | step 0 |
| **J2 Run** | start API + worker, confirm both are alive and correctly wired | step 1 |
| **J3 Connect** | base URL + token for every subsequent call | step 2 |
| **J4 Open** | create the project, post the brief, get a plan | step 3 |
| **J5 Decide** | converse to an intent, approve the intent gate, approve the cycle draft | step 4 |
| **J6 Watch** | follow execution: which task, which attempt, why it is waiting | step 5 |
| **J7 Publish** | record a disposition, verify the run against the git chain | step 6 |
| **J8 Reset** | delete the plan and its evidence, restore the repo, re-run | step 7 |
| **J9 Intervene** | pause, resume, retry a task, retry a stage, edit, replan when a run goes sideways | "Interventions" |

---

## 1. Setup — catalogs and configuration

Serves **J1**. All routes token-guarded.

| Capability | App entry | Route | Frontend | Tests | Status | Priority |
|---|---|---|---|---|---|---|
| List capabilities | `capability_repo` | `GET /api/capabilities` | `CapabilitiesSection` | `test_api.py` | full | critical |
| Register capability | `capability_repo` | `POST /api/capabilities` | `CapabilitiesSection` | `test_api.py` | full | critical |
| Edit capability | `capability_repo` | `PUT /api/capabilities/{capability_id}` | `CapabilitiesSection` | `test_api.py` | full | critical |
| Delete capability | `capability_repo` | `DELETE /api/capabilities/{capability_id}` | `CapabilitiesSection` | `test_api.py` (404 + guard) | full | critical |
| List agents | `agent_repo` | `GET /api/agents` | `AgentsSection` | `test_api.py` | full | critical |
| Register agent | `agent_repo` | `POST /api/agents` | `AgentsSection` | `test_api.py`, `test_reference_repos.py` | full | critical |
| Edit agent binding | `agent_repo` | `PUT /api/agents/{agent_id}` | `AgentsSection` | `test_api.py` | full | critical |
| Delete agent | `agent_repo` | `DELETE /api/agents/{agent_id}` | `AgentsSection` | `test_api.py` | full | critical |
| Read default agent | `agent_repo` | `GET /api/agents/default` | `AgentsSection` | `test_api.py` | full | critical |
| Set default agent | `agent_repo` | `POST /api/agents/{agent_id}/default` | `AgentsSection` | `test_api.py` | full | critical |
| List providers | `provider_repo` | `GET /api/providers` | `ProvidersSection` | `test_api.py` | full | critical |
| Add provider + key | `provider_repo`, `secret_store` | `POST /api/providers` | `ProvidersSection` | `test_api.py`, `test_secret_store.py` | ui-partial ([G7](#g7)) | critical |
| Edit provider / rotate key | `provider_repo`, `secret_store` | `PUT /api/providers/{provider_id}` | `ProvidersSection` | `test_api.py` | ui-partial ([G7](#g7)) | critical |
| Delete provider (bind-guarded) | `provider_repo` | `DELETE /api/providers/{provider_id}` | `ProvidersSection` | `test_api.py` (409) | full | critical |
| List models | `model_repo` | `GET /api/models` | `ProvidersSection` | `test_api.py` | full | critical |
| Add model | `model_repo` | `POST /api/providers/{provider_id}/models` | `ProvidersSection` | `test_api.py` | ui-partial ([G7](#g7)) | critical |
| Rename model / set capacity | `model_repo` | `PUT /api/models/{model_id}` | `ProvidersSection` | `test_api.py` | ui-partial ([G7](#g7)) | critical |
| Delete model (bind-guarded) | `model_repo` | `DELETE /api/models/{model_id}` | `ProvidersSection` | `test_api.py` (409) | full | critical |
| List projects | `project_repo` | `GET /api/projects` | `ProjectsSection`, `Plans` | `test_api.py` | full | critical |
| Create project (binding validated) | `project_repo`, `repository_binding` | `POST /api/projects` | `ProjectsSection`, `Plans` | `test_api.py`, `test_repository_binding.py` | full | critical |
| Edit project (binding validated) | `project_repo`, `repository_binding` | `PUT /api/projects/{project_id}` | `ProjectsSection` | `test_api.py`, `test_repository_binding.py` | full | critical |
| Delete project | `project_repo` | `DELETE /api/projects/{project_id}` | `ProjectsSection` | `test_api.py` | full | critical |
| Read config scope | `config_repo` | `GET /api/config/{scope}` | `ReasonerSection`, `RunnerSection` | `test_api.py` | full | critical |
| Set config key | `config_repo` | `PUT /api/config/{scope}/{key}` | `ReasonerSection`, `RunnerSection` | `test_api.py` | full | critical |
| Unset config key | `config_repo` | `DELETE /api/config/{scope}/{key}` | — | `test_api.py` | api-only | post-launch |

`orchestrate seed demo`, `db upgrade`, `config get|set|list` and `plan list|show`
are CLI-only by design (J1 runs before an HTTP client exists). Not a gap.

## 2. Readiness and health

Serves **J2**.

| Capability | App entry | Route | Frontend | Tests | Status | Priority |
|---|---|---|---|---|---|---|
| API liveness + version | — | `GET /health` | — | `test_api.py` | api-only | critical |
| Whole-installation readiness | `routers/readiness.py` (composes the validators below) | `GET /api/readiness` | — | `test_readiness.py` | api-only | critical |
| Reasoner wiring check | `reasoner/factory.validate_reasoner_config` | `GET /api/reasoner/status` | `ReasonerSection` | `test_api.py`, `test_reasoner_factory.py` | full | critical |
| Runner mode, bindings, binary probes | `runtime/factory`, `dependency_checker` | `GET /api/runner/status` | `RunnerSection` | `test_api.py`, `test_agent_runner_factory.py` | full | critical |
| Worker liveness (is anyone running?) | plan/goal lease | — (per-plan `worker_lease` only) | — | `test_worker_pool.py` | hidden ([G10](#g10)) | critical |
| Repository / workspace readiness | `repository_binding`, `ProjectWorkspaceResolver.repository_path_for` | `GET /api/projects/{project_id}/readiness` | — | `test_readiness.py`, `test_repository_binding.py` | api-only | critical |

## 3. Plan lifecycle

Serves **J4**, **J8**.

| Capability | App entry | Route | Frontend | Tests | Status | Priority |
|---|---|---|---|---|---|---|
| Open the project's plan with a brief | `create_plan.open_project_plan` | `POST /api/plans` | `Plans` composer | `test_api.py`, `test_default_cyclic_execution.py` | full | critical |
| List plans | plan repo (promoted columns) | `GET /api/plans` | `Plans` | `test_api.py` | full | critical |
| Read the plan document | plan repo + execution ledger | `GET /api/plans/{plan_id}` | every view | `test_api.py` and most integration tests | ui-partial ([G2](#g2), [G3](#g3)) | critical |
| Delete the plan and everything under it | `delete_plan.delete_plan` | `DELETE /api/plans/{plan_id}` | — | `test_delete_plan_leaves_nothing.py`, `test_delete_plan.py` | api-only ([G8](#g8)) | critical |
| Bind a legacy unbound plan to a project | `bind_project.bind_legacy_project` | `POST /api/plans/{plan_id}/project-binding` | — | `test_api.py` | api-only | compat |
| Open a plan without a project (pre-cyclic) | `create_plan.create_plan` | — | — | `test_full_cycle.py` (skipped) | legacy | compat |

## 4. Planning and gates

Serves **J5**.

| Capability | App entry | Route | Frontend | Tests | Status | Priority |
|---|---|---|---|---|---|---|
| Discovery conversation turn | `conversation.discovery_message` | `POST /api/plans/{plan_id}/discovery/message` | `ChatPanel` | `test_api.py`, `test_conversation_and_planning.py` | full | critical |
| Replan conversation turn | `conversation.replanning_message` | `POST /api/plans/{plan_id}/replanning/message` | `ChatPanel` | `test_api.py`, `test_replan_loop.py` | full | critical |
| Persisted chat history | `ChatStore` | `GET /api/plans/{plan_id}/chat` | `ChatPanel` | `test_api.py`, `test_chat_repository.py` | full | critical |
| Propose an intent | `cyclic_planning.propose_intent` | `POST /api/plans/{plan_id}/intent` | `queries` (`proposeIntent`) | `test_api.py`, `test_cyclic_project_plan.py` | full | critical |
| Revise an intent | `cyclic_planning.revise_intent` | `PUT /api/plans/{plan_id}/intent` | `queries` (`reviseIntent`) | `test_api.py` | full | critical |
| Cancel an intent | `cyclic_planning.cancel_intent` | `DELETE /api/plans/{plan_id}/intent` | `GatePanel` | `test_api.py` | full | critical |
| Approve the intent gate | `cyclic_planning.approve_intent` | `POST /api/plans/{plan_id}/intent/approve` | `GatePanel` | `test_api.py`, `test_default_cyclic_execution.py` | full | critical |
| Submit a cycle draft | `cyclic_planning.submit_cycle_draft` | `POST /api/plans/{plan_id}/cycle-draft` | — (reasoner-authored) | `test_api.py` | api-only | critical |
| Revise a cycle draft | `cyclic_planning.revise_cycle_draft` | `PUT /api/plans/{plan_id}/cycle-draft` | `queries` (`reviseCycleDraft`) | `test_api.py` | full | critical |
| Cancel a cycle draft | `cyclic_planning.cancel_cycle_draft` | `DELETE /api/plans/{plan_id}/cycle-draft` | `queries` (`cancelCycleDraft`) | `test_api.py` | full | critical |
| Approve the draft → activate the cycle | `cyclic_planning.activate_cycle` | `POST /api/plans/{plan_id}/cycle-draft/approve` | `GatePanel` | `test_api.py`, `test_default_cyclic_execution.py` | full | critical |
| Read recorded planning attempts | `planning_artifacts` store | `GET /api/plans/{plan_id}/planning-artifacts` | — | `test_api.py`, `test_planning_artifacts.py` | api-only | critical |
| Discard recorded planning attempts | `planning_artifacts` store | `DELETE /api/plans/{plan_id}/planning-artifacts` | — | `test_api.py` | api-only | post-launch |
| JIT goal-contract enrichment | `PlanningHandler.enrich_goal_contract` | — (worker-driven) | — | `test_conversation_and_planning.py` | hidden by design | — |

## 5. Execution visibility

Serves **J6**.

| Capability | App entry | Route | Frontend | Tests | Status | Priority |
|---|---|---|---|---|---|---|
| Domain event stream | outbox relay → `SSEBroker` | `GET /api/events` | `queries` SSE bridge | `test_sse_stream.py`, `test_outbox_relay.py` | full | critical |
| Attempt/run timeline | `execution_records` | `GET /api/plans/{plan_id}/attempts` | `ConsoleDock` | `test_api.py`, `test_execution_records.py` | full | critical |
| One attempt's captured log | `process_supervisor.attempt_log_path` | `GET /api/plans/{plan_id}/attempts/{attempt_id}/log` | — | `test_api.py` | api-only ([G5](#g5)) | critical |
| Live attempt log (SSE tail) | `process_supervisor.follow_attempt_log` | `GET /api/plans/{plan_id}/attempts/{attempt_id}/log/stream` | — | `test_sse_stream.py` | api-only ([G5](#g5)) | critical |
| Fine-grained agent telemetry | `agent_events` store | `GET /api/plans/{plan_id}/agent-events` | `DetailPanel` | `test_api.py` | full | critical |
| Token/run roll-up | `observations` | `GET /api/metrics` | `Activity` | `test_api.py`, `test_observation_repository.py` | full | post-launch |
| Current work + TDD stage | `Plan.activity`, `tdd_stage` | in `GET /api/plans/{plan_id}` | `LifecycleRail` | `test_cyclic_project_plan.py` | full | critical |
| Worker liveness for this plan | `worker_lease` projection | in `GET /api/plans/{plan_id}` | — | `test_worker_pool.py` | api-only ([G10](#g10)) | critical |

## 6. Capacity and waiting

Serves **J6** — the "waiting, recovering automatically" half of Phase 5's rule.

| Capability | App entry | Route | Frontend | Tests | Status | Priority |
|---|---|---|---|---|---|---|
| Provider circuit / backoff state | `provider_capacity`, `RuntimeCircuit` | `provider_waiting` in `GET /api/plans/{plan_id}` | — | `test_cyclic_worker.py`, `test_backoff_gate.py` | api-only ([G3](#g3)) | critical |
| Planning backoff + retry-at | `PlanningOperation` | `planning_operation` in `GET /api/plans/{plan_id}` | `ConsoleDock` | `test_reasoner_backoff.py` | ui-partial | critical |
| Per-provider/model in-flight ceiling | `AgentSpec`/provider rows | `POST`/`PUT /api/providers…`, `PUT /api/models/{model_id}` | — | `test_reference_repos.py` | ui-partial ([G7](#g7)) | post-launch |
| Tier-ordered agent reselection | `ExecutionHandler`, `model_role` | — (automatic) | — | `test_goal_parallel_execution.py` | hidden by design | — |

## 7. Recovery and intervention

Serves **J9**.

| Capability | App entry | Route | Frontend | Tests | Status | Priority |
|---|---|---|---|---|---|---|
| Graceful pause | `pause_resume.pause_plan` | `POST /api/plans/{plan_id}/pause` | `LifecycleRail` | `test_api.py`, `test_pause_resume.py` | full | critical |
| Resume (manual pause only) | `pause_resume.resume_plan` | `POST /api/plans/{plan_id}/resume` | `LifecycleRail` | `test_api.py`, `test_pause_resume.py` | full | critical |
| Retry one named failed task | `pause_resume.retry_task` | `POST /api/plans/{plan_id}/retry` | `DetailPanel`, `LifecycleRail` | `test_api.py`, `test_pause_resume.py` | full | critical |
| Retry a blocked planning stage | `pause_resume.retry_planning_stage` | `POST /api/plans/{plan_id}/retry-stage` | `LifecycleRail` | `test_api.py`, `test_agent_binding_recovery.py` | full | critical |
| Surgical structural edit | `apply_edit.apply_edit` | `POST /api/plans/{plan_id}/edits` | `DetailPanel` | `test_api.py`, `test_contract_editing.py` | ui-partial ([G4](#g4)) | critical |
| Holistic replan | `request_replan.request_replan` | `POST /api/plans/{plan_id}/replan` | `queries` (`replanMidRunning`) | `test_api.py`, `test_replan_loop.py` | full | critical |
| Change the retry budget | `update_retry_policy.update_retry_policy` | `POST /api/plans/{plan_id}/retry-policy` | — | `test_retry_policy_update.py`, `test_api.py` | api-only | critical |
| Per-goal blocks and their resolutions | `Plan.goal_blocks`, `block_policy` | `goal_blocks` in `GET /api/plans/{plan_id}` | — | `test_goal_blocks.py`, `test_block_policy.py` | api-only ([G2](#g2)) | critical |
| Plan-wide block and its resolutions | `Plan.block`, `block_policy` | `block` in `GET /api/plans/{plan_id}` | `Overview`, `AttentionItem` | `test_block_report.py`, `test_block_policy.py` | full | critical |
| "Is this block mine or the orchestrator's?" | `block_policy.requires_human` | — (`PlanBlock` has no such field to serve) | — | `test_block_policy.py` | hidden — already owned by Phase 5, item 1 | critical |
| Bounded automatic repair | `contract_repair`, `promotion_failures`, `agent_feedback` | — (automatic) | — | `test_execution_handler_unpromotable_goal.py` | hidden by design | — |
| Skip / abandon a wedged task | `Plan.abandon_task` | — | — | `test_transitions.py` | hidden ([G12](#g12)) | post-launch |

## 8. Evidence, repository output and publication

Serves **J7**.

| Capability | App entry | Route | Frontend | Tests | Status | Priority |
|---|---|---|---|---|---|---|
| Frozen test bundle + accepted evidence | `Task.test_bundle`, `verification_evidence` | inside `active_cycle` of `GET /api/plans/{plan_id}` | — | `test_tdd_execution.py`, `test_pre_existing_check_execution.py` | api-only ([G9](#g9)) | critical |
| Protected scope / foreign-check protection | `verification`, `test_identity` | inside the frozen `TaskContract` | — | `test_existing_check_protection.py`, `test_check_path_predicate.py` | api-only ([G9](#g9)) | critical |
| Promoted git refs (cycle/goal branches) | `project_workspace` | — (convention `cycle/<id>`, not served) | — | `test_git_workspace.py`, `test_drive_plan_sqlite_git.py` | hidden ([G9](#g9)) | critical |
| Record the output disposition | `cyclic_planning.record_output_disposition` | `POST /api/plans/{plan_id}/publication` | `GatePanel` | `test_api.py`, `test_default_cyclic_execution.py` | full | critical |
| Recorded disposition + output reference | `Cycle.output_disposition/_reference` | inside `cycles` of `GET /api/plans/{plan_id}` | — | `test_api.py` | api-only ([G9](#g9)) | critical |
| Export a run's evidence bundle | `backend/scripts/export_plan_runs.py` | — (CLI only) | — | `test_plan_run_export.py` | hidden ([G9](#g9)) | critical |
| Authenticated PR / forge write | — | — | — | — | not implemented (deliberate) | post-launch |

## 9. Nine-phase compatibility surface

**Compatibility-only. Never the authority for a plan with an active cycle.**
These routes and fields exist for migrated rows and pre-cyclic clients; the live
model is `status` + `activity` + `legal_actions` (see
[plan-lifecycle.md](plan-lifecycle.md)).

| Capability | App entry | Route | Frontend | Tests | Status | Priority |
|---|---|---|---|---|---|---|
| Approve at the pre-execution gate | `control.resume_from_review` | `POST /api/plans/{plan_id}/approve` | `queries` (`approvePlan`) | `test_api.py` | legacy | compat |
| Reopen discovery from review | `control.reopen_discovery` | `POST /api/plans/{plan_id}/review/reopen` | `queries` (`reopenReview`) | `test_api.py` | legacy | compat |
| Finish the post-execution review | `control.finish_review` | `POST /api/plans/{plan_id}/review/finish` | `queries` (`finishReview`) | `test_api.py` | legacy | compat |
| Replan from review | `control.review_replan` | `POST /api/plans/{plan_id}/review/replan` | `queries` (`replanFromReview`) | `test_api.py` | legacy | compat |

Compatibility **fields** on `GET /api/plans/{plan_id}`: `phase`, `legacy_phase`,
`iteration`, and root-level `goals` (the pre-cyclic goal list; a cyclic plan's
real work is `active_cycle.goals`). `legacy_phase` has no frontend consumer;
`phase` and `iteration` are still read by `PlanCanvas`, `TopBar`, `GatePanel`,
`PhaseTimeline` and `ChatPanel` — the contradiction recorded in
[known-issues.md](known-issues.md) and owned by Phase 5.

---

## Launch-critical gaps

Every gap below is verified, has an owner phase, and names the objective test
that will prove it closed. Nothing here is a request for symmetry: each one
breaks or hides a step of a job an operator actually performs.

**Closed by P4.1** (2026-07-28), and deleted from this list per the repo rule
that a fixed defect is replaced by the test that locks it — G1 (the guard is
applied at mount time now, proven over the whole OpenAPI inventory by
`test_control_plane_auth.py`), G6 (`test_api.py::test_retry_policy_can_be_retuned_over_http`,
which also found that a zero-attempt budget was accepted), and G11
(`test_repository_binding.py` plus the runtime lock in
`test_git_workspace.py::test_a_project_that_names_a_missing_repository_is_refused_by_the_resolver`).
The numbering is left as-is: G2 still means what it meant in the Phase 3 audit.

### G2 — Per-goal blocks are invisible in the UI {#g2}

Domain un-freeze #14 made one goal's block stop only that goal. The API serves
`goal_blocks` and the frontend read model declares it
(`frontend/src/types/ui.ts:237`), but **no component reads it** — `Overview` and
`AttentionItem` render only the plan-wide scalar `block`. An operator watching a
partially-blocked plan sees "running" with no indication that a goal needs them,
which is precisely the state un-freeze #14 created.

**Owner: Phase 5.** **Test:** a plan with two goals, one blocked and no
plan-wide block, renders one attention item per entry in `goal_blocks` and
offers that block's advertised resolutions.

### G3 — `provider_waiting` is served but absent from the frontend read model {#g3}

`PlanDetailResponse.provider_waiting` (`routers/plans.py:188`) carries the
capacity circuit's state. The hand-declared read model in
`frontend/src/types/ui.ts` never declared it, so no component could render it
without a type error. Phase 5's rule — distinguish "waiting, recovering
automatically" from "needs you" — is unsatisfiable while the waiting half is
undeclared. **The declaration is restored in this PR**; rendering it is Phase 5.

**Owner: Phase 5.** **Test:** `test_plan_read_model_parity` (added here) keeps
the two in sync; the rendering test is a plan with an open circuit showing a
"waiting" affordance and no human-action prompt.

### G4 — `update_task_contract` edits are API-only {#g4}

`EditRequest` accepts eight edit types plus `update_task_contract`
(`routers/plans.py:350`), the un-freeze #17 surgical repair of a frozen
contract. The frontend's `EditBody` union (`lib/api.ts:243`) omits it, so the
one manual move at the contract boundary — the thing `contract-repair-v1`
exists to exercise — cannot be made from the UI.

**Owner: Phase 5.** **Test:** the edit dialog submits an `update_task_contract`
body for a frozen task and the plan reflects the new revision.

### G5 — The attempt log has no UI consumer {#g5}

Both the captured log and the live SSE tail are routed and tested, and neither
appears in `lib/api.ts`. J6 ("why is this attempt failing?") therefore requires
curl. The roadmap names this endpoint explicitly.

**Owner: Phase 5.** **Test:** selecting a running attempt opens the stream and
appends rendered lines; selecting a finished one renders the captured log.

### G7 — The UI silently clears provider and model capacity overrides {#g7}

`PUT /api/providers/{provider_id}` assigns `max_inflight` and `capacity_scope`
unconditionally from the body (`routers/reference.py:222`), and
`PUT /api/models/{model_id}` rebuilds the row from `{name, max_inflight}`
(`:266`). The settings forms send neither field
(`ProvidersSection.tsx:283`), so **renaming a provider or model resets its
in-flight ceiling to NULL** — silently reverting an un-freeze #16 capacity
decision made through the API. Recorded in [known-issues.md](known-issues.md).

**Owner: Phase 5** (carry the fields in the form), with Phase 4 free to make the
update partial instead. **Test:** set `max_inflight`, rename through the client
payload the UI sends, and assert the ceiling survives.

### G8 — Plan deletion has no UI {#g8}

`DELETE /api/plans/{plan_id}` is the reset half of J8 (`reset.sh` calls it) and the
only way to discard a plan's cycles, attempts, evidence, chat and telemetry. The
UI cannot do it, so the plan list only grows.

**Owner: Phase 5.** **Test:** deleting from the plan list removes the row and a
busy plan surfaces the 409 `PLAN_BUSY` refusal as a message, not a crash.

### G9 — Evidence is reachable only by reading the whole plan document {#g9}

Accepted evidence, frozen bundles and protected scope ride inside
`active_cycle`; the recorded disposition rides inside `cycles`; promoted branch
names are not served at all (`verify_run.py` reconstructs `cycle/<cycle_id>` by
convention); and the evidence bundle export is a CLI script. J7 — "show me what
was verified and where the code went" — has no first-class read surface, and
Phase 4 lists exactly this set.

**Owner: Phase 4.** **Test:** one evidence read model per cycle returning
accepted evidence refs, protected paths, promoted refs and the disposition,
asserted against a completed dry-run cycle.

### G10 — No route says whether a worker is running {#g10}

`worker_lease` answers it per plan and only while a plan is claimed. Before the
first plan — the J2 setup checklist — nothing distinguishes "worker running,
idle" from "worker never started", the single most common local-setup failure.

**Owner: Phase 4.** **Test:** a worker-health read (last poll, mode, claimed
plan count) reports a live worker and, after its lease expires, reports it as
stale.

### G12 — A wedged task cannot be skipped {#g12}

`Plan.abandon_task` exists and is driven only by exhausted-retry paths. An
operator facing a task that should not be attempted again has retry, edit and
replan — replan being the whole-cycle hammer. Recorded as post-launch: the
walkthroughs have not yet produced a case that the other three cannot cover.

**Owner: Phase 8** unless run evidence promotes it. **Test:** abandoning a
FAILED head task lets the goal advance to the next task.

## Non-gaps — deliberately not exposed

Recording these keeps a later audit from proposing them for completeness.

- **JIT enrichment, agent reselection, contract repair, promotion re-attempt,
  backoff.** Automatic by design; an operator control point here would be the
  "pause between units" the roadmap already rejected. They are observable
  through `planning_operation`, `provider_waiting` and the event feed.
- **`resolve_block` as its own route.** A block is resolved by doing the thing
  it advertises (`retry`, `retry-stage`, `edits`, `replan`, `project-binding`),
  which `block_policy.py` maps one-to-one. A generic resolve endpoint would
  create a second, weaker guard path.
- **Nine-phase transitions for cyclic plans.** Section 9 exists to be labelled,
  not extended.
- **PR / forge writes.** No authenticated forge port exists; the orchestrator
  deliberately never merges external PRs.
