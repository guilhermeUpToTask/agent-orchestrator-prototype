# Core invariants

- Dependencies point `domain -> app -> infra/api`; domain imports neither app nor adapters, and app imports no infra.
- `Plan` owns the goal/task tree and is the only transition authority.
- Navigation is derived by scanning persisted state; no cursor is stored.
- Domain time is injected.
- Optimistic concurrency rejects stale versions consistently in fake and SQLite stores.
- State and coarse domain events commit through one unit of work.
- Agent and LLM calls occur outside database transactions.
- Only ARCHITECTURE, ENRICHING, and RUNNING are claimable; pause blocks claims.
- Worker ticks report progress, not merely a successful claim.
- Outbox delivery is at-least-once; consumers deduplicate by `event_id`.
- Terminal task exhaustion pauses the plan; permanent reasoner failure may fail it.
- Previous completed goals survive replanning and remain context.
- Domain errors expose stable codes and API mapping stays centralized.
