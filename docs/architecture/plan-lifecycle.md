# ProjectPlan lifecycle

The active lifecycle is a long-lived `ProjectPlan` bound to one immutable
`ProjectDefinition.id`. Finite delivery work lives in `Cycle` objects; the
root never reaches a terminal DONE or FAILED state.

Code anchors:

- aggregate and compatibility projection:
  `backend/src/domain/aggregates/planner_orchestrator.py`
- proposals, drafts, gates, blocks, cycles, and statuses:
  `backend/src/domain/entities/planning_artifacts.py`
- goal/task verification contracts:
  `backend/src/domain/entities/execution_contracts.py`
- deterministic navigation:
  `backend/src/domain/services/navigation.py`
- transactional lifecycle commands:
  `backend/src/app/use_cases/cyclic_planning.py`
- worker routing:
  `backend/src/app/use_cases/advance_plan.py`

## Root status

`PlanStatus` is the only persisted root lifecycle status:

| Status | Meaning |
|---|---|
| `running` | autonomous work may claim and advance one atomic action |
| `paused` | a human pause is fully settled |
| `waiting` | intent, architecture, or publication review is required |
| `blocked` | a structured exhausted/permanent failure needs resolution |
| `idle` | no active cycle or open proposal needs work |

`activity` is derived from the open intent, CycleDraft, gate, block, active
cycle, earliest nonterminal goal, and current task. It is not stored as a
second workflow enum. The API also supplies `status_reason` and
`legal_actions`; the frontend renders those facts instead of rebuilding
transition rules.

## Versioned planning artifacts

1. Conversation and project/repository context produce an `IntentProposal`.
2. An exact-revision `ReviewGate` allows approve, edit, or cancel.
3. Approval makes architecture worker-claimable.
4. The architecture reasoner submits an ordered `CycleDraft` with stable
   local goal keys and real dependency keys.
5. A second exact-revision gate allows approve, edit, or cancel.
6. Approval validates the base plan version, maps local dependency keys to
   generated goal ids, and atomically activates a finite `Cycle`.

Editing a subject invalidates its old gate and opens a replacement gate for
the incremented revision. A stale gate id or subject revision is rejected.
Replan drafts keep accepted work unchanged until approval; activation
supersedes the source cycle atomically.

## Execution and verification

Only the earliest nonterminal goal by stable position can advance. A failed
or backing-off head task, an incomplete dependency, a gate, a block, or a
pause request prevents later goals from running.

JIT enrichment freezes a `GoalContract` and ordered `TaskContract` objects.
Each task declares stable criteria, goal-criterion mappings, scope,
capabilities, commands, and one of `tdd`, `characterization`, or
`executable_check`. TestBundle and VerificationEvidence are revision-bound;
semantic edits increment the task revision and invalidate both.

Purpose-specific reasoner sessions expose shared read tools plus exactly one
submission tool:

- intent discovery: `submit_intent_proposal`
- cycle architecture: `submit_cycle_draft`
- goal enrichment: `submit_goal_contract`

Submission handlers collect DTOs only. Application services re-read, validate,
mutate the aggregate, and persist state plus domain events in one UoW/outbox
transaction. Test-author and implementer bindings resolve through the existing
`AgentSpec` registry using mandatory `test_authoring` and `implementation`
capabilities.

## Pause, retry, and failure

A pause request during an active run promotes `pause_requested=true`, blocks
new claims, and leaves status RUNNING until that run finalizes. Finalization
settles PAUSED and clears the request. With no active run, pause settles
immediately.

`resume()` only removes a manual pause. It never retries work, rewinds
identity, clears backoff, or resolves a block. Targeted retry names one failed
task; if it matches an active execution block it resolves only that block and
starts a new retry-policy cycle while preserving absolute attempt identity.

Transient failures arm durable backoff. Exhausted or permanent cyclic failures
open a `PlanBlock` with stage, goal/task/revision/run identity, evidence
references, an operator-safe explanation, and legal resolutions. The root is
BLOCKED and unclaimable.

## Completion and publication

After all goal work is accepted, cycle verification opens one publication
gate. The allowed dispositions are `open_pr`, `merge`, `retain_branch`,
and `discard`. A successful non-discard disposition requires a recorded
output reference. Recording the approved disposition completes (or cancels)
the cycle and returns the ProjectPlan to IDLE.

## Legacy compatibility

`PlanPhase`, legacy conversation routes, and root `goals` remain a
read/transition compatibility projection while migrated clients and rows are
retired. They are not the authority for a plan with an active cycle.

Migration 0009 records the honest phase-derived status in
`legacy_mapped_status` but quarantines every unbound legacy row as BLOCKED
with an operator-visible `project_binding` block. Because the released schema
contains no authoritative plan-to-project relation, no project id is guessed.
After an explicit operator binding, the recorded mapped status can be restored.
Historical goals/tasks and phase data remain readable.
