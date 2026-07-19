# Cyclic ProjectPlan implementation record

Date: 2026-07-14

This record captures the repository-grounded impact map, ordered packages, and
verification used for ADR-003. It supersedes the lifecycle-freeze instruction
in the 2026-07-13 strategy while retaining that report as behavioral evidence.

## Impact map

| Concern | Existing authority inspected | Refactor impact |
|---|---|---|
| Aggregate/lifecycle | `planner_orchestrator.Plan`, `PlanPhase` | project binding, root status, cycles, versioned review artifacts, blocks, graceful pause |
| Navigation/retry | `navigation.next_action`, `RetryPolicy`, Task attempts | strict head-goal barrier, targeted retry, absolute identity |
| Worker safety | execution/planning handlers, worker loop | active-cycle routing, mid-run heartbeat, stale revision/run/version guard |
| Persistence | JSON Plan table, fake/SQLite repos, Alembic | promoted project/status/pause fields, claim predicate/index, legacy quarantine |
| Reasoner | Reasoner port, OpenAI/stub adapters, tool loop | purpose profiles and DTO-only intent/draft/contract submissions |
| Agent runtime | AgentSpec registry, CLI runner/factory | capability-based test-author/implementer roles; existing provider adapters reused |
| Verification | TaskResult and Git workspace | frozen contracts/TestBundle, protected hashes, orchestrator commands/evidence |
| Git | plan/task worktrees | project resolver and cycle/goal/task hierarchy with promotion barriers |
| API/UI | plan router, OpenAPI, React plan surface | root status/reason/activity/legal actions, gate/block/TDD/evidence state |
| Documentation | decision log and architecture guides | ADR-003 unfreeze, cyclic lifecycle guide, current compatibility debt |

The repository contains `ProjectDefinition` but no persisted rich
`ProjectSpec` model or forge publication adapter. Those absences constrain
cycle-wide canonical commands and authenticated PR creation; they are recorded
without duplicating configuration or performing unauthorized external writes.

## Ordered packages executed

0. Characterized phase, claim, retry, lease, reasoner-tool, Git, migration,
   API, and frontend behavior; recorded ADR-003 and decision 43.
1. Fixed strict ordering, targeted retry, monotonic attempts/run ids, heartbeat,
   stale finalization, and owned process-group cleanup.
2. Added PlanStatus, Cycle, IntentProposal, CycleDraft, ReviewGate, PlanBlock,
   pause request, derived activity/reason/legal actions, and nonterminal roots.
3. Added project/status/pause persistence, one-plan-per-project constraint,
   fake/SQLite parity, migration 0009, and deterministic legacy mapping tests.
4. Added exact purpose tool profiles, worker-driven CycleDraft architecture,
   JIT GoalContract enrichment, stable dependency validation, and artifact-only
   reasoner submission.
5. Added verification strategies/contracts, task revisions, role resolution,
   authoritative test checkpoints, RED/characterization/check rules, protected
   test/scope enforcement, independent command evidence, and completion guards.
6. Added project-scoped workspace resolution and
   `cycle/<id> → goal/<id> → task/<id>/<run-id>` promotion barriers.
7. Added lifecycle use cases/routes/read models, regenerated OpenAPI/TypeScript,
   and explicit root status/pause-requested/gate/block/TDD rendering.
8. Replaced obsolete lifecycle documentation, refreshed graphify, and ran the
   full backend/static/frontend/migration/TDD quality gates.

## Legacy mapping

Migration 0009 records:

- DONE → IDLE
- FAILED → BLOCKED
- RUNNING, ENRICHING, ARCHITECTURE → RUNNING
- REVIEW, AWAITING_REVIEW → WAITING
- DISCOVERY, REPLANNING → WAITING

Because no legacy column authoritatively identifies a ProjectDefinition, every
legacy row is then quarantined as root BLOCKED/project_id NULL with an active
`project_binding` PlanBlock. The honest mapped status is retained in
`legacy_mapped_status`; an explicit operator binding restores it. Plan JSON,
goals, tasks, results, and legacy phase remain readable. No approval,
publication, cycle ownership, or project identity is fabricated.
