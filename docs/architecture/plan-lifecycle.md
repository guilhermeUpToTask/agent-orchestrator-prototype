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

Planning proceeds through three versioned subjects, in order:

**`IntentProposal` → `CycleDraft` → active `Cycle`.**

### IntentProposal

`IntentProposal` is a **domain entity**
(`backend/src/domain/entities/planning_artifacts.py`), not a dedicated API
DTO. It carries the human-reviewed objective, scope, constraints, exclusions,
proposal kind (`initial` | `replan`), `base_plan_version`, optional replan
`source_cycle_id`, and a `revision` counter. The plan detail response field
`intent_proposal` on `PlanDetailResponse`
(`backend/src/api/routers/plans.py`) surfaces this domain model directly as
the OpenAPI schema.

`revision` **starts at 1**. The Plan aggregate is the only mutator
(`backend/src/domain/aggregates/planner_orchestrator.py`):

- `revise_intent` requires the replacement's `revision` to equal
  `current.revision + 1` (monotonic +1 only). Any other step raises
  `InvalidEditError`.
- `approve_intent` requires the caller-supplied revision to match the open
  proposal exactly. A missing proposal or stale revision raises
  `InvalidEditError`.

That exact-revision check is the optimistic-concurrency mechanism for the
**human intent review gate** (paired with the open `ReviewGate`'s
`subject_revision`). It is distinct from `Plan.version` CAS, which guards
worker-vs-edit races on the plan document as a whole (see
[data-model.md](data-model.md#the-version-cas-optimistic-concurrency)).

### Cycle path

1. Conversation and project/repository context produce an `IntentProposal`.
2. An exact-revision `ReviewGate` allows approve, edit, or cancel.
3. Approval makes architecture worker-claimable.
4. The architecture reasoner submits an ordered `CycleDraft` with stable
   local goal keys and real dependency keys. Every dependency must precede its
   dependent by stable position; forward edges are rejected before activation
   because position is the scheduling barrier.
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
capabilities. Agents and their advertised capabilities remain user-managed at
runtime; role resolution never requires a default agent when an explicit match
exists.

## Pause, retry, and failure

A pause request during an active run promotes `pause_requested=true`, blocks
new claims, and leaves status RUNNING until that run finalizes. Finalization
settles PAUSED and clears the request. With no active run, pause settles
immediately.

`resume()` only removes a manual pause. It never retries work, rewinds
identity, clears backoff, or resolves a block. Targeted retry names one failed
task; if it matches an active execution block it resolves only that block and
starts a new retry-policy cycle while preserving absolute attempt identity. A matching
provider-capacity retry also clears only the referenced runtime/provider/model
circuit. A reasoner block uses the separate planning-stage retry command, which
resolves that block and clears its durable planning backoff.

An agent-capability block uses the same operator command after registry repair:
the application resolves every frozen task against the live registry outside
the plan transaction, then atomically binds all tasks and resolves the block.
If any role remains uncovered, no task binding or plan state changes.

Every advertised block resolution is executable. `edit_task` opens only the
blocked task for semantic correction; saving the edit invalidates its
revision-bound evidence, and continuing resolves the block without consuming a
second retry transition. `start_replan` opens a new versioned intent proposal.
The source cycle remains visible and immutable while the proposal and side-by-side
CycleDraft are reviewed. Completed source work is supplied to the reasoner and
must not be recreated; activation alone supersedes the source cycle.

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
An operator restores the plan through
`POST /api/plans/{plan_id}/project-binding`; the command verifies the project,
binds the immutable identity, resolves the block, restores the recorded mapped
status, and writes `BlockResolved` in the same transaction. Historical
goals/tasks and phase data remain readable.
