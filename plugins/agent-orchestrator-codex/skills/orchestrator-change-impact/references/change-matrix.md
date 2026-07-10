# Change matrix

| Surface | Coupled surfaces | Focused verification |
|---|---|---|
| `backend/src/domain/` | app handlers/use cases, fakes, decision log | orchestration unit suite on memory + SQLite |
| `backend/src/app/` | ports, fakes, container, API callers | relevant orchestration tests + integration |
| `backend/src/infra/db/` | tables, migration, UoW/repositories, fakes | migration, repository, truth tests |
| `backend/src/infra/runtime/` | agent registry, provider/model catalog, secrets, status | runner factory + taxonomy integration tests |
| `backend/src/infra/reasoner/` | two-method port, config factory, telemetry | reasoner units + scripted LLM full cycle |
| `backend/src/api/` | exception map, OpenAPI, generated TS, queries/UI | API tests + contract generation + frontend build |
| `backend/alembic/` | tables and deployed SQLite state | migration-chain tests from empty and predecessor |
| `frontend/src/types/generated/` | OpenAPI exporter; never hand edit | regenerate and drift check |
| `frontend/src/types/ui.ts` | aggregate read model and UI consumers | search consumers + TypeScript build |
| event/outbox payloads | relay, SSE names, frontend dedup/listeners | outbox relay + API + frontend |
| known issue fix | regression test and known-issues removal | targeted regression + full gate |

Always finish backend-affecting changes with `make check`. Finish frontend or API-contract changes with `npm run build` and deterministic generation.
