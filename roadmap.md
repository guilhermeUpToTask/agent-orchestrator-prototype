The roadmap is divided into **six stages**, each producing a stable milestone.

---

# Phase 1 — System Observability & Stability

Goal: make the current system **production-debuggable** before adding intelligence.

Most agent frameworks fail because they add intelligence before observability.

## Features

### 1. Execution telemetry

Every agent run must store:

```
execution_logs/
  task_id/
     prompt.txt
     agent_output.txt
     tool_calls.json
     execution_time.json
     token_usage.json
```

Why:

* debugging failures
* replaying runs
* improving prompts

---

### 2. Event log persistence

Store all events as append-only logs:

```
events/
  2026-03-07.log
```

Example event:

```json
{
 "timestamp": "...",
 "event": "task_started",
 "task_id": "task_012"
}
```

---

### 3. Run replay tool

CLI command:

```
orchestrator replay task_012
```

Reconstructs:

```
task definition
agent prompt
workspace state
```

---

### 4. Task validator

Pipeline:

```
planner → validator → task manager
```

Validator checks:

* schema correctness
* file paths exist
* allowed_files valid
* test command exists

---

### Phase 1 milestone

System becomes **auditable and debuggable**.

---

# Phase 2 — Repository Context Index

Goal: prevent LLM context overload.

Instead of loading entire repository.

## Features

### 1. Repository indexer

Build structured metadata.

Output:

```
repo_index/
  file_tree.json
  symbol_index.json
  api_map.json
  dependency_graph.json
```

Example:

```json
{
 "file": "auth/service.ts",
 "exports": ["login", "logout"],
 "imports": ["user_repo"]
}
```

---

### 2. Code search API

Agents query:

```
search_symbol("UserRepository")
```

Instead of reading all files.

---

### 3. Targeted context packaging

Worker creates agent context:

```
task.yaml
relevant files
symbol definitions
test files
```

Only necessary context is passed.

---

### Phase 2 milestone

Agents scale to **large codebases**.

---

# Phase 3 — Planner Agent

Goal: introduce **goal-driven development**.

Planner sits above task manager.

Architecture:

```
User
 ↓
Planner
 ↓
Task Manager
 ↓
Workers
```

---

## Features

### 1. Planner loop

Cycle:

```
observe project state
analyze tasks
generate new tasks
```

Pseudo loop:

```
while project_not_finished:
    observe_state()
    plan_next_tasks()
```

---

### 2. Goal definition

User defines:

```
goal.yaml
```

Example:

```yaml
goal: add authentication system
constraints:
  framework: fastapi
  database: postgres
```

---

### 3. Task generation

Planner generates:

```
tasks/*.yaml
```

Example:

```yaml
task_id: auth_001
objective: implement user model
allowed_files:
 - models/user.py
test_command: pytest tests/test_user.py
```

---

### Phase 3 milestone

System becomes **goal-driven instead of manually scripted**.

---

# Phase 4 — Persistent Project State

Goal: prevent **planner drift**.

Planner must maintain a long-term memory of the project.

---

## Features

### Project state directory

```
project_state/
  roadmap.md
  architecture.md
  decisions.md
  backlog.yaml
```

---

### Planner update cycle

Planner must:

```
read project_state
update architecture
append decisions
generate tasks
```

---

### Decision log

Example:

```
Decision 003:
Authentication will use JWT tokens
Reason: stateless scaling
```

Planner must respect previous decisions.

---

### Phase 4 milestone

Planner maintains **architectural coherence**.

---

# Phase 5 — Task Graph Execution

Goal: support **task dependencies**.

Current system likely executes tasks independently.

Add DAG support.

---

## Features

### Task dependency field

```yaml
depends_on:
  - auth_001
```

---

### DAG scheduler

Task manager must ensure:

```
dependencies completed before execution
```

Example:

```
auth_model → auth_service → auth_api
```

---

### Parallel task execution

Tasks without dependencies run simultaneously.

---

### Phase 5 milestone

System executes **complex development workflows**.

---

# Phase 6 — Advanced Agent Ecosystem

Goal: introduce specialized agents.

Instead of a single generic agent.

---

## Agent types

### Planner agent

Strategic reasoning.

---

### Coder agent

Writes implementation code.

---

### Test agent

Writes tests.

---

### Review agent

Checks code quality.

---

### Refactor agent

Improves architecture.

---

Example pipeline:

```
planner
   ↓
coder
   ↓
test_writer
   ↓
reviewer
   ↓
merge
```

---

# Phase 7 — Continuous Development Loop

Goal: full autonomous development cycle.

Loop:

```
user goal
 ↓
planner
 ↓
task graph
 ↓
execution
 ↓
test verification
 ↓
planner evaluation
 ↓
next tasks
```

---

# Optional Phase 8 — Multi-Agent Collaboration

Not required initially but possible later.

Add:

```
multiple agents solving same task
result voting
confidence scoring
```

Similar to Symphony ideas.

---

# Final Target Architecture

```
User
 ↓
Goal Definition
 ↓
Planner Agent
 ↓
Project State Manager
 ↓
Task Validator
 ↓
Task Graph Scheduler
 ↓
Workers
 ↓
Agent Executors
 ↓
Workspace
 ↓
Tests
 ↓
Result Analyzer
 ↓
Planner Feedback
```

---

# Estimated Development Order

Practical sequence:

```
Phase 1 observability
Phase 2 repo indexing
Phase 3 planner
Phase 4 project state
Phase 5 DAG scheduler
Phase 6 specialized agents
```

This avoids building an unstable planner too early.

---

# Realistic System Capability at the End

Your system could autonomously:

```
receive a project goal
design architecture
generate development tasks
write code
write tests
verify results
iterate
```

Which is essentially an **AI-driven autonomous development pipeline**.
