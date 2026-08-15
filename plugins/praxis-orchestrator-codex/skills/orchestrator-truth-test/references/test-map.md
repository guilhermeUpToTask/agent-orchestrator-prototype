# Test map

| Change | Tests |
|---|---|
| Plan transitions/navigation/edit/replan/backoff/pause | `tests/unit/orchestration/` through `env_factory` |
| UoW, CAS, outbox, repository behavior | dual-backend suite + repository integration tests |
| worker lease/crash/finalize | worker units + `test_drive_plan_sqlite_git.py` |
| FastAPI route/error/security | `tests/integration/test_api.py` |
| git worktree begin/commit/discard | `test_git_workspace.py` |
| runner or failure taxonomy | `test_runner_taxonomy.py`, `test_agent_runner_factory.py` |
| reasoner runtime | `tests/unit/reasoner/`, scripted LLM full cycle |
| migration/table | `test_migrations.py` plus affected repositories |
| OpenAPI/frontend | API tests, generate twice, drift check, `npm run build` |
| full lifecycle | `test_full_cycle.py` and, when relevant, `test_full_cycle_llm.py` |

`pytest -m llm` is opt-in and never part of normal CI.
