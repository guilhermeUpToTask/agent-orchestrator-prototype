# Documentation

The map of everything written down about this system. Start at the root [`README.md`](../README.md) for orientation; come here to go deeper.

## Structure

```text
docs/
├── architecture/     HOW the system works today — one file per subsystem, diagram-first
├── decisions/        WHY it works that way — ADRs and the consolidated decision log
├── legacy/           WHAT the old backend had — preserved for reintroduction analysis
└── history/          The paper trail — archived plans, analyses, pre-refactor docs
```

## Architecture — how it works

Read in this order the first time:

| Doc | Covers |
|---|---|
| [overview.md](architecture/overview.md) | Process topology, the hexagonal layers, the dependency rule, where every concern lives |
| [plan-lifecycle.md](architecture/plan-lifecycle.md) | The nine-phase machine, the three drivers, conversational phases, the two gates, the append-only replan loop |
| [execution-model.md](architecture/execution-model.md) | The worker loop, the per-plan lease, the two-transaction crash choreography, retries/backoff, the git-worktree workspace |
| [events-and-observability.md](architecture/events-and-observability.md) | Transactional outbox, the relay, SSE, agent telemetry, structured logging, secrets hygiene |
| [data-model.md](architecture/data-model.md) | Every SQLite table, the plan-as-document decision, envelope-encrypted secrets, migrations |
| [frontend.md](architecture/frontend.md) | The React dashboard: views, data layer, SSE bridge, type generation |
| [known-issues.md](architecture/known-issues.md) | **Verified defects and fragile spots**, with `file:line` evidence — read before operating or reviewing |

The backend's **frozen port contracts** (exact SQL shapes, method signatures, API→use-case map) live next to the code: [`backend/docs/INTEGRATION_GUIDE.md`](../backend/docs/INTEGRATION_GUIDE.md). Per-layer and per-package READMEs live inside `backend/src/` — they are the closest documentation to each line of code.

## Development and delivery

| Doc | Covers |
|---|---|
| [development.md](development.md) | Hardened local setup, seeding, supervised startup, parameters, secrets hygiene, and CI-parity checks |
| [git-flow.md](git-flow.md) | Branch naming, pull requests, Conventional Commits, CI, releases, and hotfixes |

## Decisions — why it works that way

| Doc | Covers |
|---|---|
| [decisions/README.md](decisions/README.md) | Index + how to add a decision |
| [decision-log.md](decisions/decision-log.md) | The consolidated numbered log — every locked design decision with its rationale |
| [adr-001-concurrency-lease.md](decisions/adr-001-concurrency-lease.md) | Sequential-per-plan now; the lease granularity as the future parallelism switch |
| [domain-design-decisions.md](decisions/domain-design-decisions.md) | The ten domain-layer design questions, resolved at the Phase-0 freeze (kept as the record) |

## Legacy — the old backend's features

[legacy/pre-refactor-backend.md](legacy/pre-refactor-backend.md) documents every capability the pre-refactor system had that the current one deliberately does not — PR gate, project spec governance, the decision gate, the old plan lifecycle, the Redis event topology — each with *what it did, why it was shelved, what the reintroduction seam is*. Use it to decide what comes back.

## History — the paper trail

[history/](history/README.md) archives the project's planning documents (dated, with the model that produced each), the debugging analyses that drove the fixes, and the pre-refactor documentation set. Nothing in `history/` describes the current system — it exists so decisions stay traceable and old designs stay recoverable.

## Keeping docs honest

- A doc that contradicts the code is a bug in the doc — fix it in the same PR that changes the behavior.
- `architecture/` files cite real paths (`backend/src/...`); when you rename code, grep the docs.
- Unimplemented ideas do not belong in `architecture/` — they go to [`ROADMAP.md`](../ROADMAP.md).
- Superseded plans move to `history/planning/`, never deleted.
