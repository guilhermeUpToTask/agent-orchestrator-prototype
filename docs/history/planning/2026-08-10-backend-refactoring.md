# Backend and documentation refactoring

**Created 2026-08-10.** Scoped from measurement taken from the tree that day.
Supersedes the shorter first draft of this file.

## The rule this plan runs by

**Behaviour-preserving, in independently-green steps.** 1400 tests pass today
and that suite is the entire safety net; a refactor verifiable only at the end
is a rewrite in disguise. Every step ends with `ruff` + `mypy` + full suite
green, and a commit. A step that cannot be made green alone is too big.

**No behaviour changes ride along.** A bug found mid-refactor gets its own
commit, before or after — never inside. Mixing them makes a bisect useless
exactly when it is most needed.

---

# Part 1 — Backend

## What is already right, and must not be "improved"

Worth stating first, because a refactor that damages these would be a net loss:

- **The dependency rule holds exactly.** `domain` importing `app`/`infra`/`api`:
  **0**. `app` importing `infra`: **0**. The hexagonal boundary is real, not
  aspirational. Ports-and-adapters is doing its job.
- **Permanent null adapters** (`NoEnvironment`, `NoSandbox`, `NoForge`) are a
  correct Null Object usage — they encode a supported state, not a TODO.
- **The transactional outbox and the SQLite lease** are load-bearing and
  well-tested through the dual-backend `env_factory`.

## SOLID findings, with the measurement behind each

### SRP — `ExecutionHandler` is a god class

**2,133 lines, 44 methods**, spanning roughly eight responsibilities:
main-repo integrity, contract repair, the test-author stage, the implementation
stage, verification, goal promotion, acceptance runs, and agent
selection/admission. It contains the two longest functions in the codebase:
`_finalize_failure` (**303 lines**) and `finalize` (**288**).

This is the code that decides what gets merged, so it is simultaneously the
most important and the most dangerous thing in the tree.

### OCP — adding one runtime is shotgun surgery

Adding `codex` on 2026-08-09 required edits in **four** files:
`cli_runner.py` (the class), `factory.py` (`RUNTIME_TYPES` + a dispatch
branch), `dependency_checker.py` (the probe), and `routers/reference.py`. A new
runtime should be one registration, not four coordinated edits.

**Pattern:** a runtime *registry* — each runtime a descriptor (command builder,
env mapping, whether it needs an API key, its protocol parser, its install
hint), registered once. Dispatch, validation and probing all read the registry.

### Parameter Objects — `drive_goal` takes **20 parameters**

| function | params |
|---|---|
| `use_cases/run_worker.drive_goal` | **20** |
| `use_cases/run_worker.drive_plan` | 15 |
| `infra/runtime/process_supervisor.supervise_process` | 14 |
| `use_cases/advance_plan.__init__` | 14 |
| `handlers/execution_handler.__init__` | 14 |

`drive_goal` has 11 optional collaborators defaulting to `None`. Every optional
collaborator is a branch, and the `| None` on a dependency means "sometimes this
object is only half-built". Bundle them into an explicit execution-context
object so the dependency set is named once and constructed complete.

### ISP — not currently a problem

The ports are narrow and purpose-specific (`Reasoner` has four transforms, each
with a distinct reason to exist). No fat-interface finding. Do not split them
for symmetry.

### LSP — one thing to watch, not fix

`CodexRunner` needs no API key while its siblings do. That asymmetry is
**correct and should stay visible** in the registry work above — it is a real
difference, not an inconvenience to hide behind a flag.

## DRY findings

**117 duplicated 6-line blocks.** Concentrated in four places:

1. **`cli_runner.py`** — the same constructor boilerplate 4–5×. `CliAgentRunner`
   holds 367 shared lines; the concrete runners are 86/40/87/48 lines and differ
   almost entirely in `_build_cmd` and `_env`. Resolved by the runtime registry
   above.
2. **`infra/db/*_repository.py`** — the same session/transaction preamble ×4
   across `execution_record`, `goal_promotion`, `acceptance_run`,
   `plan_repository`. A small shared base or context manager.
3. **`api/routers/*.py`** — the same dependency-injection preamble ×5. A shared
   router dependency.
4. **`infra/git/workspace.py` + `project_workspace.py`** — ×5, git subprocess
   invocation. One `_git()` helper, already half-present.

## Ordered backend tasks

### Task 1 — adversarial fakes (do this first)

Not cosmetic, and the only item that changes the odds of the *next* bug being
caught. Three defects escaped 1384 green tests on 2026-08-09, and none was
missed through carelessness — each was **inexpressible**:

| Defect | Why no test could fail on it |
|---|---|
| Implementer bound to a test-author agent | `DummyAgentRunner` was handed `spec` and discarded it |
| Reasoner never submitted | `FakeLLMClient` replays a script; it cannot *choose* to keep reading |
| Empty/errored stream misread | Scripted CLIs emit exactly the shapes the taxonomy expects |

**A fake that discards an input cannot fail on it.**

1. Audit every fake for inputs accepted then dropped; record the list first.
2. Add adversarial behaviours as **opt-in** modes so existing tests are
   unchanged: a runner returning a distinct identity per `run_role`; an LLM
   client that never calls the terminal tool; one returning an empty completion
   with zero usage; a CLI carrying an in-band error on a zero exit.
3. One regression test per historical defect.

**Done when** each defect has a test that fails if its fix is reverted.

### Task 2 — the runtime registry (OCP + the largest DRY win)

Collapses `cli_runner.py` duplication and makes a new runtime one registration.
Keep codex's no-key path explicit.

### Task 3 — split `ExecutionHandler`, in risk order

1. Pure helpers first (`_main_repo_stray_paths`, `_test_author_path_allowed`,
   `_raise_on_infrastructure_exit`, `_attempts_against_budget`, `_jitter_unit`).
2. Agent selection + admission into a collaborator.
3. Goal promotion + acceptance into a collaborator.
4. **The two finalize monsters LAST**, once the file is readable — they hold the
   check-before-act idempotency and revalidation.

Public surface (`handle`, `handle_goal`, `finalize`) does not change.

### Task 4 — parameter objects for the worker drive path

`drive_goal`/`drive_plan`/`advance_plan`. Mechanical once Task 3 lands.

### Task 5 — router split and the repository/router DRY items

`plans.py` (1626 lines) split by concern with **identical route paths** —
`test_capability_matrix.py` fails if any route moves, so this refactor is
self-verifying. Rename `pause_resume.py`, which now holds five unrelated use
cases.

## Explicitly NOT in scope

- **The FROZEN aggregate.** 1270 lines and it stays. Splitting it is a domain
  change needing a decision-logged un-freeze, and "the file is long" is not a
  capability gap.
- **`openai_reasoner.py` (1475).** Cohesive, and not while the execution path is
  moving. Revisit after Task 3.
- **Performance.** P8.6 owns latency against its own baseline. A refactor that
  also claims a speedup can prove neither.

---

# Part 2 — Documentation

**182 markdown files, 30,796 lines.** The problem is not volume everywhere; it
is concentrated.

| area | files | lines |
|---|---|---|
| `docs/superpowers/plans` | 7 | **7031** |
| `docs/history` | 28 | 6307 |
| `docs/superpowers/specs` | 7 | 2511 |
| `ROADMAP.md` | 1 | **1917** |
| `docs/architecture` | 8 | 1609 |
| `docs/decisions` | 6 | 1288 |
| `docs/guides` | 9 | 1198 |

## Incorrect claims found (CLAUDE.md — the costliest file to be wrong)

CLAUDE.md states it OVERRIDES default behaviour, so an error there misdirects
every session. Three are already confirmed:

1. **The repository tree is wrong.** It shows `backend/src/domain/`,
   `backend/src/app/`, `backend/src/infra/`. `backend/src/` **does not exist** —
   the package is `backend/praxis_orchestrator/`. Every path in that tree is
   unusable as written.
2. **Un-freeze count stale** — says "through un-freeze #19"; #20 landed
   2026-08-10.
3. **`runtime_type` list stale** — `pi | claude | gemini | dry-run`, missing
   `codex`.

### Task 6 — verify every factual claim in CLAUDE.md and `docs/architecture/`

Not a proofread: each claim naming a path, config key, route or count gets
checked against the code, and either corrected or deleted. Where a claim is
mechanically checkable, prefer a test over prose — `test_fixture_docs_contract.py`
and `test_capability_matrix.py` are the precedent.

## Slop and deletion candidates

**`docs/superpowers/plans/` — 7031 lines, and the single largest concentration.**
These are *execution* plans; once executed they are history, not reference. Four
exceed 1100 lines each (P8.1 at 2065, P4.3 at 1671, P8.5 at 1431, P4.1 at 1165).
Executed plans should move to `docs/history/` — which the repo already defines as
the immutable archive — leaving only in-flight plans live. **Nothing is deleted
outright**; a plan carries the reasoning behind shipped decisions.

**`ROADMAP.md` — 1917 lines.** It has accreted delivery narrative that belongs
in history once a phase closes. Its own stated job is "everything planned but
not yet implemented (+ do-not-do)". Delivered-phase detail should be summarised
to its decision and pointed at the archive.

**`docs/history/` — 6307 lines: leave alone.** It is explicitly immutable. Size
is not a defect for an archive, and rewriting it would destroy the record.

### Task 7 — move executed plans to history, then compress ROADMAP

Order matters: moving first shows how much of ROADMAP is duplicated narrative.

### Task 8 — the doc-discipline check

The repo already asserts "a doc contradicting the code is a bug in the doc."
Extend the existing contract tests so the mechanically-checkable subset —
declared paths existing, named config keys existing, documented routes being
served — fails the build rather than relying on review.

## Exit criteria

- Every step green and committed independently; `main` never red.
- No public API, route or config-key change — proven by
  `test_capability_matrix.py` and `test_legal_actions_contract.py`.
- `ExecutionHandler` under ~800 lines, collaborators separately testable.
- A new runtime is **one** registration.
- Each 2026-08-09 defect locked by a test that fails on revert.
- Every path, key and count in CLAUDE.md and `docs/architecture/` verified
  against the code, with the checkable subset test-locked.
- **No claim in this plan is asserted without a number**, including the claim
  that the refactor worked.
