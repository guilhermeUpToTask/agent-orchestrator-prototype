# Phase 10B — the rename to `praxis-orchestrator`, scoped honestly

**Date:** 2026-08-11
**Status:** scope only. No rename work has been done, and none should start
until the open item at the bottom is cleared.
**Decision:** the project is renamed to **Praxis Orchestrator** — distribution
`praxis-orchestrator`, Python package `praxis_orchestrator`, CLI `praxis`.

ROADMAP: *"Scope the rename honestly before committing to it… Some of those are
user-visible state on existing installs and need a migration or a compatibility
alias, not a `sed`."* This is that scope.

---

## Why this name

`aipom` is a Pokémon: charming, unsearchable, and it tells a visitor nothing.
That was the known problem, and it understated the case twice over — it is also
**a Nintendo / Game Freak mark**, which makes its removal a legal hygiene item
rather than a branding preference (Group D). The audit then found a third
problem, and it is the one that makes the rename unavoidable.

**`agent-orchestrator` on PyPI is already taken — by a competing project in this
exact category.**

```
name    : agent-orchestrator
version : 2.0.0
summary : Orquestrador de Agentes de IA para Desenvolvimento de Software
author  : Agent Orchestrator Team <team@agent-orchestrator.dev>
home    : https://github.com/luhfilho/agent-orchestrator
```

An AI agent orchestrator for software development, at 2.0.0, holding the name
this repository's `pyproject.toml` declares. This project has never published:
`release-please.yml` builds a wheel and attaches it to a GitHub Release, and
nothing in CI touches PyPI. So `pip install agent-orchestrator` today installs
**someone else's tool**, and the first attempt to publish would have failed.

The rename is therefore not cosmetic. The current distribution name is
unusable.

### Availability of the chosen name

Verified 2026-08-11 against live registries. Methods were validated against a
known-registered and a known-free control first — an earlier pass reported
everything as taken because `rdap.org` answers 302 and the GitHub API answers
403 when rate-limited, and neither is a 404.

| surface | value | status |
|---|---|---|
| PyPI | `praxis-orchestrator` | free |
| npm | `praxis-orchestrator` | free |
| GitHub org | `praxis-orchestrator` | free |
| domain | `praxisorchestrator.dev` | free |
| domain | `praxisorchestrator.com` | free |

Five of five. Bare `praxis` is taken on all five, which is why the compound is
the name: the distinctive head is unavailable alone, and the category tail
(`orchestrator`) is what makes the compound both free and self-explanatory. A
reader does not need to know what *praxis* means, because *orchestrator* already
said what the thing is — the same shape as Argo Workflows or Tekton Pipelines.

---

## Surface inventory

Counts are tracked-file occurrences at `2cfb35f`.

### Group A — mechanical rename, no user impact

Internal identifiers. A careful search-and-replace plus a test run is the whole
job.

| surface | occurrences | files |
|---|---|---|
| Python package `agent_orchestrator` → `praxis_orchestrator` | 2066 | 344 |
| Distribution/repo name `agent-orchestrator` → `praxis-orchestrator` | 756 | 101 |
| `AIPOM` in titles, docs and the OpenAPI description | 24 | 22 |

The package rename is the largest number and the lowest risk: the project has
never been published, so **no external importer exists**. There is no
deprecation shim to write, because there is nobody to deprecate for.

### Group B — per-attempt runtime variables (mechanical, but read the note)

Eight of the thirteen `ORCHESTRATOR_*` variables are injected by the runner into
each agent subprocess and live only for that attempt:

`ORCHESTRATOR_ATTEMPT_ID` (78), `ORCHESTRATOR_ATTEMPT_NUMBER` (56),
`ORCHESTRATOR_PLAN_ID` (48), `ORCHESTRATOR_GOAL_ID` (48),
`ORCHESTRATOR_TASK_ID` (47), `ORCHESTRATOR_RUN_ID` (47),
`ORCHESTRATOR_SCOPE` (43), `ORCHESTRATOR_PROJECT_ID` (2)

Nothing persists them, so renaming to `PRAXIS_*` needs no migration. **The
note:** they are part of the contract with an agent runtime, so any prompt,
skill file or wrapper script that reads them renames in the same commit. Grep
`fixtures/`, `demos/` and `.orchestrator/` before assuming the package is the
only reader.

### Group C — user-visible state on existing installs

This is the group that needs a migration or an alias, and the reason this
document exists.

| surface | current | risk if renamed naively |
|---|---|---|
| state directory | `~/.orchestrator` | holds `orchestrator.db` — **every plan, cycle, and encrypted secret**. A renamed default silently starts an empty install and the operator's work "disappears". |
| master key | `ORCHESTRATOR_MASTER_KEY` | if unset under the new name, the secret store cannot decrypt. Every provider key becomes unreadable, and CLAUDE.md already warns that generating a second key **orphans the store**. |
| API token | `ORCHESTRATOR_API_TOKEN` | unset under the new name = the control plane silently becomes **open**. A rename that turns auth off is the worst failure mode in this list. |
| home override | `ORCHESTRATOR_HOME` | points at the real state dir on any non-default install. |
| DB URL | `ORCHESTRATOR_DB_URL` | same. |
| env file | `~/.orchestrator-env` (mode 600) | where the guest keeps all of the above. |

**`secret://` URIs are NOT affected.** They are `secret://provider/<id>` and
`secret://forge/<project_id>` — no product name in the scheme
(`infra/db/secret_ref.py`). An earlier draft of this scope listed them as needing
migration; that was wrong, and checking cost one grep.

### Group D — every trace of `aipom`, which is a third party's mark

**Aipom is a Pokémon — a Nintendo / Game Freak / The Pokémon Company mark.**
That reframes this group entirely. It is not a findability problem to be tidied
up alongside the package rename; it is somebody else's trademark sitting in a
repository about to be launched publicly, and it comes out **completely**.

47 tracked files. The string has spread further than the guest name suggests:

| form | count | where |
|---|---|---|
| `aipom-dev` | 39 | libvirt domain, hostname, `infra/dev-vm/`, CI, docs |
| `AIPOM` | 25 | titles, OpenAPI description, CLI help, README |
| `aipom` | 20 | prose across docs |
| `aipom-acceptance-*` | 18+ | container name prefix, and its tests |
| `aipom-planner` | 3 | **the npm package name** in `frontend/package.json` |
| `aipom-userns` | 5 | the guest's user-namespace config |
| `aipom-probe` | 4 | dev-VM verification |
| `aipom-vite-cache`, `aipom-playwright*`, `aipom-cycle-e*` | 4 | frontend build/e2e artefacts |

Renaming the guest means a **rebuild**, not an edit:
`make -C infra/dev-vm destroy && make up`, then re-seed the six-agent roster with
`seed-agents.py` and confirm `make verify` still returns 8 of 8.

The container prefix lives in `infra/environment/container_environment.py` and is
asserted in `tests/integration/test_container_environment.py` — which Phase 10A
changed to snapshot-and-diff, so the prefix is now read from one place instead of
matched as a literal in two.

#### Two places "every mention" cannot reach, and they need your decision

1. **Git history.** The string is in commit messages, past diffs, and tags going
   back to the first commit. Removing it means `git filter-repo` and a
   force-push: every clone breaks, every existing PR and commit link rots, and
   the audit trail this project's whole discipline rests on is rewritten. My
   recommendation is **don't** — scrub the working tree, leave history, and
   accept that a mark appearing in historical commit messages of a
   never-published project is a materially smaller exposure than shipping it in
   an installable artifact. But it is a real residue and you should decide it
   knowingly rather than discover it later.

2. **Recorded run evidence.** `demos/static-site-v1/runs/*/worker-log.txt` and
   `.orchestrator/runtime-runs/*/logs/*.out` contain `aipom-dev` because that was
   genuinely the hostname when those runs executed. Editing them silently
   falsifies evidence that the launch plan intends to point at as proof. Two
   honest options: replace the string and add a one-line header saying the
   hostname was substituted in the rename, or re-run the demo on the renamed
   guest and publish that instead. The second is cleaner and the demo takes 13
   minutes.

---

## Migration design for Group C

The rule: **an existing install must keep working with no operator action, and
must never silently lose state or drop auth.**

### 1. Environment variables — read both, prefer the new

One resolver, used everywhere the environment is read (the composition root,
`infra/container.py`, is already the only place that reads it):

```
PRAXIS_MASTER_KEY   else  ORCHESTRATOR_MASTER_KEY   (+ deprecation warning)
PRAXIS_API_TOKEN    else  ORCHESTRATOR_API_TOKEN    (+ deprecation warning)
PRAXIS_HOME         else  ORCHESTRATOR_HOME         (+ deprecation warning)
PRAXIS_DB_URL       else  ORCHESTRATOR_DB_URL       (+ deprecation warning)
```

The legacy name is honoured for at least one minor release and logged once at
boot, never per read. **The API token alias is the load-bearing one**: without
it, an upgrade turns a guarded control plane into an open one, and nothing in
the product would report that as an error — `security.py` treats an unset token
as "open in local dev" by design.

A regression test asserts each legacy name still resolves, and specifically that
a legacy `ORCHESTRATOR_API_TOKEN` still guards the API.

### 2. State directory — adopt, never recreate

Resolution order for the default (when no `*_HOME` is set):

1. `~/.praxis` exists → use it.
2. `~/.praxis` absent **and** `~/.orchestrator` exists → **use `~/.orchestrator`
   in place** and log once that the location is legacy.
3. Neither exists → create `~/.praxis` (fresh install).

Deliberately **not** a move or a copy. A move breaks a rollback to the previous
version; a copy duplicates an encrypted secret store, which is the one file that
must never exist twice. Adoption-in-place is reversible and keeps exactly one
database.

An explicit `praxis migrate-home` can move it later for anyone who wants the
tidy path — opt-in, verbose, and refusing to run while a worker holds a lease
(the same guard `delete_plan` already uses, `PLAN_BUSY`).

### 3. CLI entry point — ship both

`praxis` is the new entry point. `orchestrate` stays registered for one minor
release, printing a one-line deprecation to stderr and delegating. Both are
declared in `[project.scripts]`, so an operator's existing scripts keep running.

---

## Sequencing

1. **Clear the trademark item below.** Nothing starts before this.
2. Register the name on PyPI, npm, and GitHub, and the two domains. Registering
   before renaming costs nothing and removes the risk of losing the name
   mid-migration.
3. **Group D first, not last.** It was sequenced last while it was a branding
   chore; as a third party's mark it is the item with a deadline, and it is
   independent of the name that replaces it. Rebuild the guest, re-seed the
   roster, re-run `make -C infra/dev-vm verify` for its 8 of 8, and decide the
   two history/evidence questions above.
4. Group A + B in one commit: package, distribution, titles, per-attempt vars.
   Large diff, mechanical, `mypy` and the 1509-test suite are the check.
5. Group C in a second commit, with its regression tests. Small diff, all the
   risk.
6. Docs, fixtures, demos and README in the same PR as the code that moved —
   per the repo's own docs-discipline rule, and `test_documented_paths_exist.py`
   (now covering source comments too) will fail the build if any are missed.

---

## Open item — trademark, and it is not mine to close

**Registry availability is not freedom to operate.** Nothing in this document is
a trademark check, and I cannot perform one.

The specific concern to search: **Praxis Framework**, an existing project and
programme management methodology. It is adjacent to this product's domain, which
is exactly where a mark is most likely to be asserted. Also worth clearing:
Praxis (ETS teacher-certification exams, a well-known US mark) and Praxis Labs.

Registries to search, classes **9** (software) and **42** (SaaS):

- USPTO TESS — US word marks
- EUIPO — EU word marks
- the relevant national registry if you intend to trade elsewhere

Until that returns clean, treat `praxis-orchestrator` as **provisional**. Every
group above is written so the name is a parameter, not an assumption.

---

## Not in scope

- **Git history rewriting.** See Group D — recommended against, but it is a
  decision to take deliberately, not a silent omission.
- Any behaviour change. A rename that also fixes something is a rename nobody
  can review.

### A reversal worth recording

An earlier draft of this document put the archived `docs/history/` and
`docs/superpowers/specs/` records out of scope, reasoning that they are dated
accounts of what was true at the time and that rewriting them falsifies the
record — the same argument that kept them out of the Phase 10A path checker.

**That argument does not survive the trademark framing.** It is sound for a path
that moved, where the old path is simply a fact about the past. It is not sound
for a third party's mark, where the exposure is the presence of the string
itself and its being historical is no defence. The archived docs are renamed
with the rest.

The distinction that survives: **prose in an archived document** gets renamed,
because it describes the project. **Recorded machine output** (`worker-log.txt`,
`*.out`) is evidence, and gets either an explicit substitution note or a re-run
— never a silent edit.
