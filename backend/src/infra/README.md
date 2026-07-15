# Infrastructure Layer

Adapters behind the ports the application exercises, plus the **composition root**. Everything with an I/O dependency — SQLite, git, subprocesses, LLM HTTP — lives here. `infra` may import `app` and `domain`; nothing imports `infra` except the entrypoints (API, CLI, worker) and the container's consumers.

Two rules apply everywhere in this layer:

1. **The environment is read only in `container.py`.** Adapters receive paths/keys/factories as constructor arguments; if you find `os.environ` deeper than the composition root, that's a bug.
2. **Adapter semantics must match the in-memory fakes** (`src/app/testing/fakes.py`) — detached aggregates, the CAS shape, lease expiry. The dual-backend truth suite (`tests/support.py`) enforces this; when you change an adapter contract, change the fake in the same PR.

## Folder map

```
infra/
├── container.py        AppContainer — the composition root. Lazy cached properties;
│                       new_unit_of_work() per worker/request (a UoW isn't thread-safe).
│                       The ONLY reader of ORCHESTRATOR_HOME / PROJECT_REPO_DIR / etc.
├── clock.py            SystemClock (datetime.now(timezone.utc)) — the Clock port.
├── errors.py           InfrastructureError (stable `code`) + UnauthorizedError.
│
├── db/                 Everything SQLite.
│   ├── engine.py       One place for operational policy: WAL, synchronous=FULL,
│   │                   foreign_keys=ON, busy_timeout — attached per pooled connection.
│   ├── tables.py       Schema (see docs/architecture/data-model.md for the ER view).
│   ├── unit_of_work.py SqliteUnitOfWork — re-enterable: fresh Session per `with` block;
│   │                   commit = state + outbox atomically.
│   ├── plan_repository.py  The document store + version CAS + the LEASE
│   │                   (claim_one_unit / heartbeat / release — own short sessions,
│   │                   called OUTSIDE the UoW by the worker loop).
│   ├── outbox.py       Staged rows on the UoW's live session (the txn IS the staging).
│   ├── observation_repository.py Typed append-only observations with provenance,
│   │                   quality, correlation, and conflict-detecting idempotency.
│   ├── agent_event_sink.py  Legacy best-effort events, marked `legacy_unknown`.
│   ├── chat_repository.py   Per-plan conversation, own short transactions.
│   ├── reference_repos.py   Catalog CRUD (agents/capabilities/providers/models/projects)
│   │                   + ConfigStore. Integrity: delete-guards (ReferencedEntityInUseError),
│   │                   provider→model cascade-down/guard-up, dangling-ref net.
│   ├── secret_store.py Envelope encryption; resolve() is the SINGLE decryption point;
│   │                   fails closed on a missing ORCHESTRATOR_MASTER_KEY.
│   └── secret_ref.py   The api_key_ref URI type.
│
├── reasoner/           The planning LLM (the two-method Reasoner port).
│   ├── factory.py      Catalog resolution: reasoner.mode stub|llm; llm fail-fasts
│   │                   (REASONER_CONFIG_INVALID → 422). Stub NEVER touches secrets.
│   ├── stub_reasoner.py    Deterministic `ask:` / `goal:/task: [caps: …]` grammar —
│   │                   drives dry-run and every non-LLM test.
│   ├── openai_reasoner.py  converse/enrich_goal on the runtime below; terminal
│   │                   submit tools; handlers re-validate ALL tool args.
│   └── runtime/        The ported agent loop: llm_client (AsyncOpenAI, transient/
│                       permanent retry classification, empty-choices guard),
│                       agent_loop (run_tool_session, {accepted:false} self-correction),
│                       context (plan→markdown renderer), prompts, tools, errors.
│
├── runtime/            Task execution (the AgentRunner port).
│   ├── factory.py      agent_runner.mode dry-run|real; real = CatalogAgentRunner
│   │                   resolving PER TASK, PER RUN from the bound AgentSpec
│   │                   (runtime_type + provider/model rows). Broken binding =
│   │                   terminal TaskFailed(AUTH_ERROR).
│   ├── cli_runner.py   One-shot subprocess runners: pi / claude / gemini. Blocking,
│   │                   hopped off the loop via asyncio.to_thread. Runners know
│   │                   NOTHING about retries/ordering.
│   ├── taxonomy.py     Process output → FailureKind. Conservative: unknown ⇒
│   │                   TOOL_ERROR (retryable).
│   ├── dummy_runner.py The dry-run runtime = the scriptable dummy from app/testing
│   │                   (re-exported here because app may not import infra).
│   └── dependency_checker.py  Binary probes for /api/runner/status + worker boot warnings.
│
├── git/workspace.py    GitBranchWorkspace — THE rollback mechanism: worktree per
│                       attempt on task/<id>/a<n> off plan/<plan_id>; commit = --no-ff
│                       merge; discard = zero trace. (LocalDirWorkspace: no isolation,
│                       currently unused — deletion scheduled, ROADMAP #7.)
│
├── worker/main.py      run_worker_forever — the claim/drive/sleep cadence, boot-time
│                       runner validation + dependency warnings. ⚠ Known issues H1/H2
│                       live here + plan_repository (see docs/architecture/known-issues.md).
│
└── cli/main.py         `orchestrate` — db upgrade · api start · worker start ·
                        config get/set/list · plan list/show · seed demo.
                        @catch_domain_errors maps DomainError codes to exit messages.
```

## Deep dives

- Execution mechanics (lease, two-txn choreography, workspace): [`docs/architecture/execution-model.md`](../../../docs/architecture/execution-model.md)
- Schema + transactions: [`docs/architecture/data-model.md`](../../../docs/architecture/data-model.md)
- The exact port contracts this layer implements: [`backend/docs/INTEGRATION_GUIDE.md`](../../docs/INTEGRATION_GUIDE.md)
