# ADR-003: Cyclic project-plan lifecycle and deterministic execution

**Status:** accepted
**Date:** 2026-07-14
**Supersedes:** lifecycle decisions 1-9, 13, 17-18, 22, 24-25, 34-35 and
the incompatible parts of unfreezes 2-3 in `decision-log.md`

## Context

The first live plan proved the prototype loop could execute, but also proved
that its terminal nine-phase aggregate is the wrong ownership boundary. A plan
was not bound to a project, independent goals bypassed a backing-off earlier
goal, resume rewound retry identity, a 330-second action outlived its lease,
agent success was accepted without deterministic evidence, and task commits
leaked incomplete goals into the integration branch.

The historical strategy recommended keeping the nine phases frozen. That
recommendation is deliberately superseded by this ADR and decision 43.

## Decision

- A `ProjectDefinition` owns exactly one long-lived `ProjectPlan`, identified by
  immutable `project_id`. Project configuration remains in the existing project
  catalog; it is not copied into the aggregate.
- Root lifecycle is `running | paused | waiting | blocked | idle`. The root is
  never terminal. Current activity is derived from open artifacts and accepted
  execution state; no stored activity enum or cursor is introduced.
- Finite work is owned by `Cycle`. Conversation produces a versioned
  `IntentProposal`, then an ordered `CycleDraft`; exact-revision `ReviewGate`s
  approve those artifacts. At most one active cycle, open draft, unresolved
  blocking gate, and active block may exist.
- Goals belong to cycles. Only the earliest non-terminal goal is eligible.
  Dependency edges retain correctness meaning and are not manufactured to
  encode the sequential scheduling barrier.
- JIT enrichment freezes a `GoalContract` and ordered `TaskContract`s. Every
  task declares `tdd`, `characterization`, or `executable_check` verification.
  Agent output is a candidate only; protected tests, commands, scope, branch
  integrity, and evidence are independently verified before integration.
- The existing agent catalog resolves `TEST_AUTHOR`, `IMPLEMENTER`, and optional
  `VERIFIER` roles by capability. The existing reasoner loop is reused with
  purpose-specific read/submission tool profiles. Submission tools produce DTOs
  and never mutate repositories.
- Pause is graceful. `pause_requested` blocks new claims immediately; an active
  atomic run may finalize, after which the root becomes `paused`. `resume`
  removes only a manual pause. Retry/block resolution/edit/replan are explicit,
  targeted commands.
- Every run has a globally unique `run_id`, monotonic absolute attempt number,
  and separate retry-cycle policy counter. Long actions renew the plan lease.
  Finalization revalidates claim/run/version/task revision before any merge.
- Git staging is project main -> cycle -> goal -> task/run. Verified work alone
  moves upward. One output disposition is recorded per cycle.
- State plus domain facts commit through the existing UoW/outbox. Operational
  observations remain best-effort and bounded outside aggregate JSON.

## Legacy migration

Revision 0009 migrates released phase rows without inventing ownership or
approval history:

| Legacy phase | Root status | Migrated activity |
|---|---|---|
| `done` | `idle` | immutable completed legacy cycle; publication recorded as unknown legacy disposition |
| `failed` | `blocked` | legacy failure block retaining available failure context |
| `running` | `running` | active migrated cycle containing existing goals/tasks |
| `review` | `waiting` | cycle-completion review gate |
| `awaiting_review` | `waiting` | cycle-draft architecture review gate |
| `enriching` | `running` | active migrated cycle at goal-enrichment activity |
| `architecture` | `running` | pending architecture work |
| `discovery` / `replanning` | `blocked` when unbound, otherwise `waiting` | compatible proposal state requiring explicit review |

All existing goal/task JSON is retained. A legacy plan is bound only when an
authoritative project reference already exists or the database has exactly one
project and the operator selected deterministic single-project migration.
Otherwise it remains readable but non-claimable with a `project_binding` block.
No global repository or fabricated project is used as a fallback.

## Consequences

This is a deliberate breaking domain migration. Phase-based endpoints, UI,
tests, and documentation must move to backend-provided status, activity, legal
actions, gates, and blocks. Released Alembic revisions remain immutable; the
new model extends the current 0008 head. Fake and SQLite semantics continue to
share the same truth tests. Paid LLM verification remains opt-in.
