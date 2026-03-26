# ✅ Roadmap Checklist (Current State)

## ✅ Implemented / Substantially Present

### 🔧 Foundation and Orchestration Core

* [x] Hexagonal/layered architecture (`domain`, `app`, `infra`)
* [x] Project-scoped filesystem state (`~/.orchestrator/projects/<project_name>/`)
* [x] Task lifecycle:

  * [x] Creation
  * [x] Assignment
  * [x] Execution
  * [x] Retry
  * [x] Pruning
  * [x] Reset
* [x] Long-running processes:

  * [x] Task manager
  * [x] Worker
  * [x] Reconciler
* [x] Runtime modes:

  * [x] Dry-run
  * [x] Real execution

---

### 📊 Observability and Execution Logging

* [x] Runtime logging wrapper for agent runtimes
* [x] JSON logs
* [x] Terminal live logs
* [x] Event journaling (`events/` per project)
* [x] Filesystem-backed execution logs
* [x] Subprocess test execution adapters

---

### 🎯 Goal-Driven Execution

* [x] Goal aggregates and repositories
* [x] Goal initialization from goal files
* [x] Goal status inspection
* [x] Goal finalization
* [x] Event-driven orchestration (`TaskGraphOrchestrator`)
* [x] Branch-level merging of successful task work

---

### 🧠 Strategic Planning Workflow

* [x] Project plan aggregate + repository
* [x] CLI flows:

  * [x] Discovery
  * [x] Architecture
  * [x] Phase review
  * [x] Status
  * [x] Decision (placeholder)
* [x] Architectural decision handling
* [x] Phase proposal handling
* [x] Planning sessions persistence
* [x] Migration toward `plan` command group

---

### 📜 Project Spec Governance

* [x] Canonical spec loading
* [x] Spec validation
* [x] Spec change workflow:

  * [x] Proposal
  * [x] Diff
  * [x] Apply
* [x] Spec-aware validation hooks

---

### 🔀 GitHub PR Integration

* [x] GitHub client adapters:

  * [x] PR creation
  * [x] Status lookup
* [x] PR-based approval/merge gating
* [x] Project-level GitHub settings separation

---

## ⚠️ Partially Implemented / Evolving

### 📦 Repository-Aware Planning Context

* [ ] Repository indexing subsystem
* [ ] Symbol graph generation
* [ ] Targeted context packaging for agents

---

### 🔁 Replay and Audit Tooling

* [ ] End-user `replay` command
* [ ] Full execution reconstruction from logs/events
* [ ] Developer-friendly debugging workflows

---

### 🔄 Autonomous Continuous Loop

* [ ] Fully autonomous execution loop
* [ ] Reduced operator intervention
* [ ] Automated approvals/decision policies

---

### 🤖 Specialized Multi-Agent Collaboration

* [ ] Agent collaboration model
* [ ] Voting/adjudication mechanisms
* [ ] Ensemble task solving strategies

---

## 🚧 Next Milestones

### 🚀 Near-Term

* [ ] Consolidate duplicated / transitional infrastructure
* [ ] Fully migrate deprecated `goals` planning commands → `plan`
* [ ] Improve documentation:

  * [ ] Goal files
  * [ ] Project plans
  * [ ] Operator approvals
  * [ ] PR workflows
* [ ] Add richer status/reporting:

  * [ ] Planner state
  * [ ] Goal progress
  * [ ] Task execution history
* [ ] Formalize replay/debug workflows

---

### 🏗️ Mid-Term

* [ ] Repository indexing + context assembly
* [ ] Stronger policy enforcement:

  * [ ] Test requirements
  * [ ] Allowed file validation
* [ ] Expand PR automation:

  * [ ] Sync
  * [ ] Review workflows

---

### 🌐 Long-Term

* [ ] Continuous adaptive planning loop
* [ ] Explicit agent specialization
* [ ] Advanced collaboration strategies
* [ ] Scale support:

  * [ ] Larger projects
  * [ ] Persistent planning memory
  * [ ] Code intelligence layer

---

## 🧾 System Positioning (Reality Check)

* [x] Task orchestrator
* [x] Goal coordinator
* [x] Spec-governed execution system
* [x] Early strategic planning engine
* [x] Logging + PR-aware multi-agent workflow foundation

