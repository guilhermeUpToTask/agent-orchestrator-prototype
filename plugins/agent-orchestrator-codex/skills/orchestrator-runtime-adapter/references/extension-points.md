# Extension points

## Agent runtime

- Port: `backend/src/domain/ports/agent_port.py`
- Implementations: `backend/src/infra/runtime/cli_runner.py`
- Selection/bindings: `backend/src/infra/runtime/factory.py`
- Failure mapping: `backend/src/infra/runtime/taxonomy.py`
- Binary/config probes: `backend/src/infra/runtime/dependency_checker.py`
- Composition: `backend/src/infra/container.py`
- Tests: runner taxonomy, agent-runner factory, full SQLite/git drive

## Reasoner

- Port: `backend/src/domain/ports/reasoner_port.py` with exactly `converse` and `enrich_goal`
- Implementations: stub and `OpenAIReasoner`
- Tool loop/client/prompts: `backend/src/infra/reasoner/runtime/`
- Catalog config: `backend/src/infra/reasoner/factory.py`
- Tests: `tests/unit/reasoner/`, factory integration, scripted LLM full cycle

## Shared rules

- Decrypt only through `SqliteSecretStore.resolve()`.
- Never log raw keys or provider payloads containing secrets.
- Broken bindings produce stable auth/config errors.
- Dry-run and stub modes never require a master key.
- Emit fine-grained runtime telemetry through the agent-event sink.
