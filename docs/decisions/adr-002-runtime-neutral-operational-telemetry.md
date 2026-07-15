# ADR-002: Runtime-neutral operational telemetry

Status: Proposed
Date: 2026-07-13
Decision owners: Agent Orchestrator maintainers
Implementation status: Phase 1 execution identity (`0007_execution_ledger`) and Phase 2 typed model-usage observations (`0008_typed_observations`) are staged; runtime-adapter instrumentation and OpenTelemetry remain unimplemented.

## Context

Agent Orchestrator executes tasks through multiple CLI/runtime integrations and may add SDK or remote runtimes later. The current architecture has:

- a `Plan` aggregate and domain events for authoritative workflow state;
- application handlers that run external work outside the aggregate transaction;
- a transactional SQLite outbox for domain-event delivery;
- a separate best-effort `agent_events` table for runtime/reasoner observations;
- Pi, Claude, Gemini, and dry-run execution adapters, with no Codex adapter yet;
- no OpenTelemetry integration.

Before implementation, the operational stream used only a generic domain-located `AgentEvent`, free-form payloads, integer attempts, random IDs, and no source or quality. Revision `0007_execution_ledger` creates stable run/attempt identity and makes incomplete attempts discoverable. Revision `0008_typed_observations` adds an application-owned observation contract and additive metadata to the existing stream; reasoner usage now distinguishes provider-reported from unavailable values. CLI runtime events remain legacy-unknown and uncorrelated, so runtime comparison, reconciliation, and policy-grade accounting are still incomplete.

Detailed evidence and the incremental plan are in [the 2026-07-13 architecture analysis](../history/analyses/2026-07-13-runtime-neutral-telemetry-architecture.md).

## Decision

Adopt runtime-neutral operational telemetry as a distinct application/infrastructure concern, with OpenTelemetry as an asynchronous projection/transport.

### Boundaries

1. Domain aggregates own business state, invariants, transitions, and past-tense domain events.
2. Application services create stable execution identities, invoke runtime-neutral ports, and translate normalized runtime outcomes into aggregate operations.
3. Runtime adapters own CLI/SDK/provider protocols, process supervision, structured output, transcripts, and runtime-specific failure evidence.
4. Operational observations remain outside aggregates and outside the domain-event hierarchy.
5. Shared serialization does not erase semantic or consistency differences among domain events, worker messages, integration events, observations, and diagnostic logs.

### Canonical ownership

Canonical data follows this hierarchy:

1. persisted aggregate state and transactional domain events are authoritative for workflow state and business outcomes;
2. internal runtime-neutral execution records and typed observations are authoritative for operational evidence and any future policy accounting;
3. OpenTelemetry represents and transports analytical projections;
4. Grafana, Jaeger, Tempo, Langfuse, Sentry, and similar systems are replaceable backends.

OpenTelemetry data is not a transactional ledger. It may be sampled, delayed, duplicated, dropped, or unavailable. It must not directly decide retry, pause, resume, billing, audit, or budget behavior.

### Execution identity and correlation

Each logical task execution episode receives a `run_id`. Each actual runtime launch receives a unique `attempt_id` and monotonic attempt number. IDs are created and persisted before launch. Automatic retries remain attempts within the logical run; an explicit retry/reopen starts a new run.

Correlation follows the repository hierarchy where identifiers exist:

```text
project_id?
  plan_id
    goal_id
      task_id
        run_id
          attempt_id
            runtime_session_id?
              model_request_id?
```

Canonical IDs are propagated through runtime-neutral requests and, where supported, environment variables, structured runtime metadata, provider metadata, and OTel context. OTel trace/span IDs do not replace canonical IDs. A bounded execution run is the default trace root; plan/goal relationships use attributes or links rather than one long-lived span.

### Operational observations

Observations are typed, append-only where practical, and carry:

- stable observation ID;
- run/attempt and workflow correlation;
- observed and recorded timestamps;
- source and observation quality;
- kind and schema version;
- stable source sequence/key where available;
- an allowlisted typed payload.

Sources include orchestrator, runtime, process, provider, log parser, and estimator. Quality distinguishes exact, reported, derived, estimated, unavailable, and legacy-unknown evidence. Missing values remain unavailable; estimated values retain estimator/version and are never represented as provider-reported.

The existing physical `agent_events` stream/table will be evolved additively rather than replaced by a second telemetry system. Compatibility APIs may retain old names during migration.

### Persistence and consistency

- Domain events remain atomic with aggregate persistence through the existing outbox.
- Execution run/attempt lifecycle records are created/finalized with the corresponding aggregate transactions and permit incomplete-run recovery.
- Operational observations use an independent, idempotent append repository so telemetry failure does not corrupt aggregate state.
- Stable observation IDs deduplicate retry/replay; late and out-of-order evidence remains possible and explicit.
- OpenTelemetry projection is asynchronous and best effort because canonical records already exist internally.

No event store is introduced for aggregates.

### Runtime capabilities

Runtime adapters expose runtime-neutral capability descriptors to application setup and reporting. Capabilities may include structured events, usage reporting, tools, streaming, model/cost identity, transcript access, and trace injection. Unsupported telemetry is unavailable, not zero. The domain does not branch on runtime capabilities.

### OpenTelemetry

OpenTelemetry SDK, semantic mappings, spans, exporters, and context handling remain under infrastructure. Domain facts and operational observations are projected separately. Duration-bearing operations become spans; point-in-time facts become events/log records or span events when scoped appropriately.

Instrumentation sends OTLP to an OpenTelemetry Collector. The Collector owns batching, filtering, redaction, sampling, normalization, routing, and backend export. Standard GenAI, process, HTTP, VCS, CI/CD, and exception conventions are used where they accurately match evidence; repository-specific attributes use an allowlisted `orchestrator.*` namespace.

### Privacy

External telemetry uses an explicit allowlist. Prompts, completions, source/file content, diffs, credentials, environment dictionaries, raw stdout/stderr, transcripts, absolute workspace paths, personal data, and arbitrary payload dictionaries are denied by default.

Raw diagnostic logs/transcripts, if retained, remain local under stricter access and retention. Redaction occurs before application export and again in the Collector. Sampling applies only to analytical projection; canonical lifecycle and policy-grade usage observations are unsampled.

## Consequences

### Positive

- Workflow truth stays independent from observability vendors and runtime protocols.
- Pi, Claude, Codex, local, SDK, and remote runtimes can provide different telemetry depth without lying about coverage.
- Interrupted and failed runs gain stable correlation and recoverable evidence.
- Internal usage can later support budgets and audits without depending on sampled traces.
- The existing outbox, aggregate, runtime factory, and `agent_events` persistence seam are reused.
- Collector-based export allows backend replacement and central privacy controls.

### Negative

- Every additional observation kind needs a typed payload, deterministic mapper, fake/SQLite parity, and compatibility treatment; the baseline schema and model-usage payload are implemented.
- Existing clients/tests that interpret absent usage as zero need a versioned compatibility transition.
- Runtime adapters require pinned protocol fixtures and ongoing maintenance as CLIs evolve.
- Provider/runtime observations can conflict or arrive late; queries must preserve provenance and apply documented precedence.
- Broader observation capture requires explicit retention and volume controls.

### Neutral/operational

- The current implementation improves reasoner usage truthfulness but does not add CLI-agent token/tool coverage.
- OpenTelemetry can be deployed later and independently.
- Project and release correlation remain nullable until authoritative ownership is added.

## Alternatives rejected

- **Use domain events for all telemetry:** operational evidence has different meaning and consistency from business facts.
- **Use traces as the canonical store:** traces are lossy and backend-dependent.
- **Create a parallel telemetry event system:** the existing `agent_events` stream can be evolved safely.
- **Put OTel types in core contracts:** this would couple business/application logic to observability infrastructure.
- **Use one vendor-neutral parser for all CLI output:** JSONL and transcript protocols are runtime-specific.
- **Estimate unavailable data by default:** estimates would distort policy and runtime comparisons.
- **Event-source the aggregate:** current JSON persistence plus outbox is sufficient; telemetry does not justify that rewrite.
- **Adopt a particular metrics/backend product now:** there is no concrete requirement, and the Collector preserves backend choice.

## Reversal

Until this change is accepted and merged, this proposed ADR may still be replaced, but revisions `0007_execution_ledger` and `0008_typed_observations` would need to be removed or superseded explicitly. After execution identities and observations ship, OpenTelemetry remains independently removable. Reversing the internal ledger would require preserving or explicitly migrating run/attempt/observation history, but would not require changing aggregate state.

The ADR should move from Proposed to Accepted only when maintainers approve the run/attempt semantics, observation-store evolution, and compatibility strategy. It becomes locked under the repository decision policy only when implementation ships.
