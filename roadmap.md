# Roadmap and current status

This file now reflects the **current state of the prototype** rather than an older purely aspirational phase list.

## Implemented or substantially present

### 1. Foundation and orchestration core

Implemented today:

- hexagonal/layered separation across `src/domain`, `src/app`, and `src/infra`
- project-scoped filesystem state under `~/.orchestrator/projects/<project_name>/`
- task creation, assignment, execution, retry, pruning, and reset flows
- long-running task manager, worker, and reconciler processes
- dry-run and real runtime modes

### 2. Observability and execution logging

Implemented today:

- runtime logging wrapper for all agent runtimes
- JSON and terminal-oriented live logs
- event journaling under each project's `events/` directory
- filesystem-backed execution logs and subprocess test execution adapters

### 3. Goal-driven execution

Implemented today:

- goal aggregates and repositories
- goal initialization from goal files
- goal status inspection and finalization
- event-driven goal orchestration through `TaskGraphOrchestrator`
- branch-level merging of successful task work into goal branches

### 4. Strategic planning workflow

Implemented today:

- persisted project plan aggregate and repository
- discovery, architecture, and phase review CLI flows
- architectural decisions and phase proposal handling
- planning sessions and project-state persistence hooks

### 5. Project spec governance

Implemented today:

- canonical project spec loading and validation
- proposal, diff, and apply workflow for spec changes
- spec-aware validation hooks for planning and execution paths

### 6. GitHub PR integration

Implemented today:

- GitHub client adapters for PR creation and status lookup
- goal review events that support PR-based approval/merge gating
- project-level GitHub settings stored separately from orchestrator config

## Partially implemented or still evolving

### Repository-aware planning context

The codebase has planning and project-state primitives, but it does **not yet** expose a full repository indexing/search subsystem with symbol graphs and targeted context packaging.

### Replay and audit tooling

Logging and event persistence exist, but there is no polished end-user `replay` command yet that reconstructs an entire execution from stored artifacts.

### Autonomous continuous loop

The pieces for tasks, goals, planning, and reviews are present, but the system still relies on explicit operator-driven commands and approvals rather than a completely autonomous continuous development loop.

### Specialized multi-agent collaboration

The registry supports multiple agents and runtime types, but there is not yet a built-in collaboration model such as voting, adjudication, or ensemble task solving.

## Suggested next milestones

### Near-term

- consolidate duplicate or transitional infrastructure pieces
- improve end-user documentation around goal files, project plans, and PR flows
- add richer status/reporting commands for planners, goals, and task execution history
- formalize replay/debugging workflows from stored logs and events

### Mid-term

- build repository indexing and targeted context assembly for planner/worker prompts
- improve policy enforcement around test requirements and allowed-file validation
- expand PR synchronization and review automation

### Longer-term

- move from operator-steered planning phases to a more continuous adaptive loop
- add more explicit agent specialization and collaboration strategies
- support larger-scale projects with richer planning memory and code intelligence

## Summary

The prototype is no longer just a task queue for coding agents. Its current state is:

- a task orchestrator
- a goal coordinator
- a spec-governed project execution system
- an early strategic planning engine
- a logging and PR-aware multi-agent workflow foundation

Future roadmap work should build on that reality rather than assuming those capabilities are still missing.
