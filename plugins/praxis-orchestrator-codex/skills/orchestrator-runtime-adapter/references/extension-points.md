# Extension points

## Agent runtime

- Port: `backend/praxis_orchestrator/domain/ports/agent_port.py`
- Implementations: `backend/praxis_orchestrator/infra/runtime/cli_runner.py`
- Selection/bindings: `backend/praxis_orchestrator/infra/runtime/factory.py`
- Failure mapping: `backend/praxis_orchestrator/infra/runtime/taxonomy.py`
- Binary/config probes: `backend/praxis_orchestrator/infra/runtime/dependency_checker.py`
- Composition: `backend/praxis_orchestrator/infra/container.py`
- Tests: runner taxonomy, agent-runner factory, full SQLite/git drive

## Reasoner

- Port: `backend/praxis_orchestrator/domain/ports/reasoner_port.py` with exactly `converse` and `enrich_goal`
- Implementations: stub and `OpenAIReasoner`
- Tool loop/client/prompts: `backend/praxis_orchestrator/infra/reasoner/runtime/`
- Catalog config: `backend/praxis_orchestrator/infra/reasoner/factory.py`
- Tests: `tests/unit/reasoner/`, factory integration, scripted LLM full cycle

## Shared rules

- Decrypt only through `SqliteSecretStore.resolve()`.
- Never log raw keys or provider payloads containing secrets.
- Broken bindings produce stable auth/config errors.
- Dry-run and stub modes never require a master key.
- Emit fine-grained runtime telemetry through the agent-event sink.
