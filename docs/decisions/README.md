# Decisions

*Why the system is the way it is. When code looks strange, the explanation is usually here — check before "fixing" it.*

| Doc | What it records |
|---|---|
| [decision-log.md](decision-log.md) | The consolidated, numbered log of every locked design decision, grouped by topic, with dates and rationale |
| [adr-001-concurrency-lease.md](adr-001-concurrency-lease.md) | The concurrency model: sequential-per-plan now; the lease *granularity* (plan → goal → task) is the intentional future parallelism switch |
| [adr-002-runtime-neutral-operational-telemetry.md](adr-002-runtime-neutral-operational-telemetry.md) | Proposed: runtime-neutral execution evidence, canonical observations, and OpenTelemetry infrastructure |
| [domain-design-decisions.md](domain-design-decisions.md) | The ten domain-layer design questions (gates, FailureKind, reopen, result history, capability ids, …) — all resolved at the Phase-0 freeze on 2026-07-02; kept in full as the option-by-option record |

## Conventions

- **A decision is locked when it ships.** Re-opening one requires a new entry that supersedes the old — never silent drift. Domain-contract changes additionally require a recorded **un-freeze** (log entry + date), because the domain is frozen.
- **Substantial architectural decisions get their own ADR** (`adr-NNN-<slug>.md`): status, decision, why, what reversal requires. Small locked calls go straight into the decision log.
- Rejected-by-design ideas live in [ROADMAP.md](../../ROADMAP.md)'s do-not-do list, so they aren't re-litigated by default.
- The raw material behind most entries — the planning documents where trade-offs were argued — is archived in [../history/planning/](../history/planning/).
