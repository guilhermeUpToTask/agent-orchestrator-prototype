# Capability-to-product coverage matrix

**What this is.** One authoritative answer to "which operator workflows does this
system support, and where is each capability exposed?" — every domain capability
traced through its application entry point, its FastAPI route, its frontend
consumer, and its tests, then classified and given a launch priority.

Produced by roadmap **Phase 3** and re-audited at the close of **Phase 5**
(2026-08-01). Every row was verified from source and executable contracts, not
inferred from prose.

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
| Add provider + key | `provider_repo`, `secret_store` | `POST /api/providers` | `ProvidersSection` | `test_api.py`, `test_secret_store.py`, `api.phase5.test.ts` | full | critical |
| Edit provider / rotate key | `provider_repo`, `secret_store` | `PUT /api/providers/{provider_id}` | `ProvidersSection` | `test_api.py`, `api.phase5.test.ts` | full | critical |
| Delete provider (bind-guarded) | `provider_repo` | `DELETE /api/providers/{provider_id}` | `ProvidersSection` | `test_api.py` (409) | full | critical |
| List models | `model_repo` | `GET /api/models` | `ProvidersSection` | `test_api.py` | full | critical |
| Add model | `model_repo` | `POST /api/providers/{provider_id}/models` | `ProvidersSection` | `test_api.py`, `api.phase5.test.ts` | full | critical |
| Rename model / set capacity | `model_repo` | `PUT /api/models/{model_id}` | `ProvidersSection` | `test_api.py`, `api.phase5.test.ts` | full | critical |
| Delete model (bind-guarded) | `model_repo` | `DELETE /api/models/{model_id}` | `ProvidersSection` | `test_api.py` (409) | full | critical |
| List projects | `project_repo` | `GET /api/projects` | `ProjectsSection`, `Plans` | `test_api.py` | full | critical |
| Create project (binding validated) | `project_repo`, `repository_binding` | `POST /api/projects` | `ProjectsSection`, `Plans` | `test_api.py`, `test_repository_binding.py` | full | critical |
| Edit project (binding validated) | `project_repo`, `repository_binding` | `PUT /api/projects/{project_id}` | `ProjectsSection` | `test_api.py`, `test_repository_binding.py` | full | critical |
| Delete project | `project_repo` | `DELETE /api/projects/{project_id}` | `ProjectsSection` | `test_api.py` | full | critical |
| Probe a repository URL before binding (P8.1) | `repository_binding.probe_remote` | `POST /api/projects/probe` | `ProjectsSection` wizard | `test_repository_binding.py` | full | critical |
| Materialize a project's clone on request (P8.1) | `ProjectWorkspaceResolver.resolve` | `POST /api/projects/{project_id}/clone` | `ProjectsSection` wizard | `test_repository_binding.py` | full | critical |
| Read a project's forge binding (P8.1) | `infra/forge/binding.read_binding` | `GET /api/projects/{project_id}/forge` | `ProjectsSection` wizard | `test_forge_binding_api.py` | full | critical |
| Bind a GitHub forge, token verified at save (P8.1) | `infra/forge/github.verify_github_token`, `secret_store` | `PUT /api/projects/{project_id}/forge` | `ProjectsSection` wizard | `test_forge_binding_api.py` | full | critical |
| Remove a forge binding and its token (P8.1) | `infra/forge/binding.clear_binding`, `secret_store` | `DELETE /api/projects/{project_id}/forge` | `ProjectsSection` wizard | `test_forge_binding_api.py` | full | critical |
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
| Whole-installation readiness | `routers/readiness.py` (composes the validators below) | `GET /api/readiness` | `ReadinessSection`, `Plans` | `test_readiness.py` | full | critical |
| Reasoner wiring check | `reasoner/factory.validate_reasoner_config` | `GET /api/reasoner/status` | `ReasonerSection` | `test_api.py`, `test_reasoner_factory.py` | full | critical |
| Runner mode, bindings, binary probes | `runtime/factory`, `dependency_checker` | `GET /api/runner/status` | `RunnerSection` | `test_api.py`, `test_agent_runner_factory.py` | full | critical |
| Worker liveness (is anyone running?) | `WorkerRegistry` heartbeat | `GET /api/workers` | `ReadinessSection` (composed check) | `test_workers_api.py`, `test_worker_registry.py`, `test_worker_pool.py` | full | critical |
| Repository / workspace readiness | `repository_binding`, `ProjectWorkspaceResolver.repository_path_for` | `GET /api/projects/{project_id}/readiness` | `ReadinessSection` | `test_readiness.py`, `test_repository_binding.py` | full | critical |

## 3. Plan lifecycle

Serves **J4**, **J8**.

| Capability | App entry | Route | Frontend | Tests | Status | Priority |
|---|---|---|---|---|---|---|
| Open the project's plan with a brief | `create_plan.open_project_plan` | `POST /api/plans` | `Plans` composer | `test_api.py`, `test_default_cyclic_execution.py` | full | critical |
| List plans | plan repo (promoted columns) | `GET /api/plans` | `Plans` | `test_api.py` | full | critical |
| Read the plan document | plan repo + execution ledger | `GET /api/plans/{plan_id}` | every view; canonical truth in `Overview`/`LifecycleRail` | `test_api.py`, `Overview.phase5.test.tsx` and integration suites | full | critical |
| Delete the plan and everything under it | `delete_plan.delete_plan` | `DELETE /api/plans/{plan_id}` | `Plans` | `test_delete_plan_leaves_nothing.py`, `test_delete_plan.py`, `api.phase5.test.ts` | full | critical |
| Bind a legacy unbound plan to a project | `bind_project.bind_legacy_project` | `POST /api/plans/{plan_id}/project-binding` | `Overview` block resolution | `test_api.py`, `api.phase5.test.ts` | full | compat |
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
| Read recorded planning attempts | `planning_artifacts` store | `GET /api/plans/{plan_id}/planning-artifacts` | `Overview` recovery history | `test_api.py`, `test_planning_artifacts.py` | full | critical |
| Discard recorded planning attempts | `planning_artifacts` store | `DELETE /api/plans/{plan_id}/planning-artifacts` | — | `test_api.py` | api-only | post-launch |
| JIT goal-contract enrichment | `PlanningHandler.enrich_goal_contract` | — (worker-driven) | — | `test_conversation_and_planning.py` | hidden by design | — |

## 5. Execution visibility

Serves **J6**.

| Capability | App entry | Route | Frontend | Tests | Status | Priority |
|---|---|---|---|---|---|---|
| Domain event stream | outbox relay → `SSEBroker` | `GET /api/events` | `queries` SSE bridge | `test_sse_stream.py`, `test_outbox_relay.py` | full | critical |
| Attempt/run timeline | `execution_records` | `GET /api/plans/{plan_id}/attempts` | `ConsoleDock` | `test_api.py`, `test_execution_records.py` | full | critical |
| One attempt's captured log | `process_supervisor.attempt_log_path` | `GET /api/plans/{plan_id}/attempts/{attempt_id}/log` | `ConsoleDock` → `AttemptLogViewer` | `test_api.py`, `api.phase5.test.ts` | full | critical |
| Live attempt log (SSE tail) | `process_supervisor.follow_attempt_log` | `GET /api/plans/{plan_id}/attempts/{attempt_id}/log/stream` | `ConsoleDock` → `AttemptLogViewer` | `test_sse_stream.py`, `api.phase5.test.ts` | full | critical |
| Fine-grained agent telemetry | `agent_events` store | `GET /api/plans/{plan_id}/agent-events` | `DetailPanel` | `test_api.py` | full | critical |
| Token/run roll-up | `observations` | `GET /api/metrics` | `Activity` | `test_api.py`, `test_observation_repository.py` | full | post-launch |
| Current work + TDD stage | `Plan.activity`, `tdd_stage` | in `GET /api/plans/{plan_id}` | `LifecycleRail` | `test_cyclic_project_plan.py` | full | critical |
| Worker liveness for this plan | `worker_lease` projection | in `GET /api/plans/{plan_id}` | `Overview` | `test_worker_pool.py` | full | critical |

## 6. Capacity and waiting

Serves **J6** — the "waiting, recovering automatically" half of Phase 5's rule.

| Capability | App entry | Route | Frontend | Tests | Status | Priority |
|---|---|---|---|---|---|---|
| Provider circuit / backoff state | `provider_capacity`, `RuntimeCircuit` | `provider_waiting` in `GET /api/plans/{plan_id}` | `Overview` | `test_cyclic_worker.py`, `test_backoff_gate.py`, `Overview.phase5.test.tsx` | full | critical |
| Planning backoff + retry-at | `PlanningOperation` | `planning_operation` in `GET /api/plans/{plan_id}` | `Overview`, `ConsoleDock` | `test_reasoner_backoff.py`, `test_planning_artifacts.py` | full | critical |
| Per-provider/model in-flight ceiling | `AgentSpec`/provider rows | `POST`/`PUT /api/providers…`, `PUT /api/models/{model_id}` | `ProvidersSection` | `test_reference_repos.py`, `api.phase5.test.ts` | full | post-launch |
| Tier-ordered agent reselection | `ExecutionHandler`, `model_role` | — (automatic) | — | `test_goal_parallel_execution.py` | hidden by design | — |

## 7. Recovery and intervention

Serves **J9**.

| Capability | App entry | Route | Frontend | Tests | Status | Priority |
|---|---|---|---|---|---|---|
| Graceful pause | `pause_resume.pause_plan` | `POST /api/plans/{plan_id}/pause` | `LifecycleRail` | `test_api.py`, `test_pause_resume.py` | full | critical |
| Resume (manual pause only) | `pause_resume.resume_plan` | `POST /api/plans/{plan_id}/resume` | `LifecycleRail` | `test_api.py`, `test_pause_resume.py` | full | critical |
| Retry one named failed task | `pause_resume.retry_task` | `POST /api/plans/{plan_id}/retry` | `DetailPanel`, `LifecycleRail` | `test_api.py`, `test_pause_resume.py` | full | critical |
| Retry a blocked planning stage | `pause_resume.retry_planning_stage` | `POST /api/plans/{plan_id}/retry-stage` | `LifecycleRail` | `test_api.py`, `test_agent_binding_recovery.py` | full | critical |
| Surgical structural edit | `apply_edit.apply_edit` | `POST /api/plans/{plan_id}/edits` | `DetailPanel`, including contract repair | `test_api.py`, `test_contract_editing.py`, `api.phase5.test.ts` | full | critical |
| Holistic replan | `request_replan.request_replan` | `POST /api/plans/{plan_id}/replan` | `queries` (`replanMidRunning`) | `test_api.py`, `test_replan_loop.py` | full | critical |
| Change the retry budget | `update_retry_policy.update_retry_policy` | `POST /api/plans/{plan_id}/retry-policy` | — | `test_retry_policy_update.py`, `test_api.py` | api-only | critical |
| Per-goal blocks and their resolutions | `Plan.goal_blocks`, `block_policy` | `goal_blocks` in `GET /api/plans/{plan_id}` | `Overview`, `AttentionItem` | `test_goal_blocks.py`, `test_block_policy.py`, `Overview.phase5.test.tsx` | full | critical |
| Plan-wide block and its resolutions | `Plan.block`, `block_policy` | `block` in `GET /api/plans/{plan_id}` | `Overview`, `AttentionItem` | `test_block_report.py`, `test_block_policy.py` | full | critical |
| Where each advertised action is served | `action_endpoints_for` | `action_endpoints` in `GET /api/plans/{plan_id}` | — | `test_legal_actions_contract.py` | api-only | critical |
| "Is this block mine or the orchestrator's?" | `block_policy.requires_human` | `requires_human` on every block in `GET /api/plans/{plan_id}` | `Overview` attention/recovery queues | `test_block_report.py`, `test_block_policy.py`, `Overview.phase5.test.tsx` | full | critical |
| Bounded automatic repair | `contract_repair`, `promotion_failures`, `agent_feedback` | — (automatic) | — | `test_execution_handler_unpromotable_goal.py` | hidden by design | — |
| Skip / abandon a wedged task | `Plan.abandon_task` | — | — | `test_transitions.py` | hidden ([G12](#g12)) | post-launch |

## 8. Evidence, repository output and publication

Serves **J7**.

| Capability | App entry | Route | Frontend | Tests | Status | Priority |
|---|---|---|---|---|---|---|
| Frozen test bundle + accepted evidence | `Task.test_bundle`, `verification_evidence` | `GET /api/plans/{plan_id}/cycles/{cycle_id}/evidence` | `CycleEvidenceSummary` | `test_cycle_evidence_api.py`, `test_tdd_execution.py` | full | critical |
| Review a cycle in review-sized units (P8.3) | `GitReviewReader`, `goal_promotions`, task evidence | `GET /api/plans/{plan_id}/cycles/{cycle_id}/review` | `CycleReviewPanel` | `test_cycle_review_api.py` | full | critical |
| Patch text for one review unit (P8.3) | `GitReviewReader.patch` | `GET /api/plans/{plan_id}/cycles/{cycle_id}/review/patch` | `CycleReviewPanel` | `test_cycle_review_api.py` | full | critical |
| Protected scope / foreign-check protection | `verification`, `test_identity` | `GET /api/plans/{plan_id}/cycles/{cycle_id}/evidence` | `CycleEvidenceSummary` | `test_cycle_evidence_api.py`, `test_existing_check_protection.py` | full | critical |
| Promoted git refs (cycle/goal branches) | `project_workspace`, `goal_promotion_repository` | `GET /api/plans/{plan_id}/cycles/{cycle_id}/evidence` | `CycleEvidenceSummary` | `test_cycle_evidence_api.py`, `test_goal_promotion_repository.py`, `test_git_workspace.py` | full | critical |
| Record the output disposition | `cyclic_planning.record_output_disposition` | `POST /api/plans/{plan_id}/publication` | `GatePanel` | `test_api.py`, `test_default_cyclic_execution.py` | full | critical |
| Recorded disposition + output reference | `Cycle.output_disposition/_reference` | `GET /api/plans/{plan_id}/cycles/{cycle_id}/evidence` | `CycleEvidenceSummary` | `test_cycle_evidence_api.py`, `test_api.py` | full | critical |
| Export all plan-run analytics and evidence | `backend/scripts/export_plan_runs.py` | — (whole-database CLI export; not the J7 cycle read) | — | `test_plan_run_export.py` | hidden | post-launch |
| One evidence read model per cycle — accepted evidence, protected scope, promoted refs, disposition | `routers/evidence.py` | `GET /api/plans/{plan_id}/cycles/{cycle_id}/evidence` | `CycleEvidenceSummary` | `test_cycle_evidence_api.py` | full | critical |
| Topology-aware delivery hand-off — where the cycle branch physically is | `routers/evidence.py::_delivery`, `project_workspace.repository_path_for` | `GET /api/plans/{plan_id}/cycles/{cycle_id}/evidence` (`delivery`) | `CycleEvidenceSummary` | `test_cycle_evidence_api.py` | full | critical |
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
real work is `active_cycle.goals`). `GatePanel` and `PhaseTimeline` read these
only when `legacy_phase` explicitly marks a compatibility row; cyclic screens
use `status`, `activity`, `legal_actions`, and `active_cycle`.

---

## Launch-critical gaps

Every gap below is verified, has an owner phase, and names the objective test
that will prove it closed. Nothing here is a request for symmetry: each one
breaks or hides a step of a job an operator actually performs.

**Closed by P4.2** (2026-07-28) — G10, by `GET /api/workers` plus the readiness
`workers` check, locked by `test_workers_api.py`. The same branch served
`requires_human` and `action_endpoints`, and its advertised-action contract test
found the domain inconsistency recorded as un-freeze #19 (decision 60).

**Closed by P4.3** (2026-07-31) — G9, by
`GET /api/plans/{plan_id}/cycles/{cycle_id}/evidence`, locked by
`test_cycle_evidence_api.py`. The read model joins accepted revision-bound
evidence, protected scope, recorded goal promotions, and disposition for any
cycle without requiring the full plan document. The whole-database
`export_plan_runs.py` analytics export remains a separate post-launch surface.

**Closed by P4.1** (2026-07-28), and deleted from this list per the repo rule
that a fixed defect is replaced by the test that locks it — G1 (the guard is
applied at mount time now, proven over the whole OpenAPI inventory by
`test_control_plane_auth.py`), G6 (`test_api.py::test_retry_policy_can_be_retuned_over_http`,
which also found that a zero-attempt budget was accepted), and G11
(`test_repository_binding.py` plus the runtime lock in
`test_git_workspace.py::test_a_project_that_names_a_missing_repository_is_refused_by_the_resolver`).
The numbering is left as-is for historical references.

**Closed by Phase 5** (2026-08-01), and deleted from this list per the same repo
rule — G2/G3 are locked by `Overview.phase5.test.tsx` and the backend parity,
block-policy, and goal-block suites; G4/G7/G8 by `api.phase5.test.ts` plus their
backend edit/reference/deletion suites; and G5 by the frontend stream-parser
contract plus `test_api.py` and `test_sse_stream.py`. Their matrix rows now name
the concrete frontend consumers and carry `full` status.

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
