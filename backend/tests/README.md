# Tests

```bash
make check                    # the gate for every change: ruff + mypy + pytest
pytest -m "not integration"   # fast unit suite
pytest -m integration         # real SQLite, real git repos, TestClient
pytest -m llm                 # cost-gated real-provider smoke — NEVER in normal CI
```

## The truth test — the suite's keystone

The orchestration tests in `unit/orchestration/` don't run once — they run **twice**, through the parametrized `env_factory` fixture (`conftest.py` + `support.py`): once against the in-memory fakes (`src/app/testing/fakes.py`) and once against the **real SQLite UnitOfWork** on a `tmp_path` database.

Why this matters: the crash-recovery-via-lease-expiry, outbox-rollback, and backoff-gate-survives-crash tests passing on real SQLite is the *proof* that transactional atomicity is real, not simulated by an obliging fake. This is the property the whole persist-first design rests on.

The corollary discipline: **fake and real adapter semantics must stay identical** — detached aggregates (a returned Plan never aliases stored state), the same CAS rejection shape, the same lease-expiry behavior. If you change an adapter contract, change the fake in the same PR, or the truth test is lying.

## Layout

```
tests/
├── support.py                 the dual-backend env factory (the truth-test machinery)
├── fakes_llm.py               FakeLLMClient — scripted AssistantTurns for reasoner tests
├── unit/
│   ├── orchestration/         the dual-backend suite: transitions, navigation,
│   │                          advance/dispatch, worker loop + crash recovery,
│   │                          backoff gate, replan loop, conversation/planning,
│   │                          use cases, edge cases
│   └── reasoner/              the LLM runtime in isolation: agent loop (terminal
│                              tools, self-correction, budgets), llm_client retry
│                              classification, context-renderer goldens, OpenAIReasoner
└── integration/
    ├── test_full_cycle.py     all nine phases + a replan loop on the STUB reasoner —
    │                          the deterministic dry-run gate
    ├── test_full_cycle_llm.py the same walk through OpenAIReasoner on FakeLLMClient
    ├── test_drive_plan_sqlite_git.py   worker + real SQLite + real git together
    ├── test_api.py            TestClient over the full app (container injected)
    ├── test_git_workspace.py  real repos in tmp_path: begin/commit/discard semantics
    ├── test_runner_taxonomy.py  scripted fake CLIs → FailureKind classification
    ├── test_agent_runner_factory.py · test_reasoner_factory.py   catalog resolution + fail-fasts
    ├── test_migrations.py · test_reference_repos.py · test_chat_repository.py
    ├── test_secret_store.py · test_outbox_relay.py
    └── test_reasoner_smoke.py  marker `llm`; needs REASONER_SMOKE_API_KEY
                                (+ optional _BASE_URL / _MODEL); one real converse turn
```

## Conventions

- **Determinism over sleeps**: `FakeClock.advance()` drives backoff and lease expiry; the `DummyAgentRunner` is scripted per task id and emits the *shared failure taxonomy*, so dry-run tests exercise the exact production retry/terminal paths.
- Always `tmp_path` for file I/O and `monkeypatch` for env vars — tests never touch `~/.orchestrator`.
- No Redis anywhere (the claim path is the SQLite lease).
- Regression discipline: every fixed bug gets a test that locks it (the gate-spin, worker-tick-spin, resurrection, and stale-version bugs all have one). When you fix an entry in [known-issues.md](../../docs/architecture/known-issues.md), add its lock here.
