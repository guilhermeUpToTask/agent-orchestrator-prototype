# Phase 10B — the rename to `praxis-orchestrator`, scoped honestly

**Date:** 2026-08-11
**Status:** **All four groups are executed** (Group D 2026-08-11; Groups A, B
and C 2026-08-15). What remains is not code — see *Executed* below.
**Decision:** the project is renamed to **Praxis Orchestrator** — distribution
`praxis-orchestrator`, Python package `praxis_orchestrator`, CLI `praxis`.
**The name is settled** (owner decision 2026-08-11, reaffirmed 2026-08-15).
The `Praxis Framework` collision was raised and accepted — a programme
management methodology is not an agent orchestrator — and it is closed.

ROADMAP: *"Scope the rename honestly before committing to it… Some of those are
user-visible state on existing installs and need a migration or a compatibility
alias, not a `sed`."* This is that scope.

---

## Why this name

The original working name was unsearchable and told a visitor nothing. That was
the known problem, and it understated the case twice over — it is also **a third
party's mark**, which makes its removal a legal hygiene item rather than a
branding preference (Group D). The audit then found a third problem, and it is
the one that makes the rename unavoidable.

**`praxis-orchestrator` on PyPI is already taken — by a competing project in this
exact category.**

```
name    : praxis-orchestrator
version : 2.0.0
summary : Orquestrador de Agentes de IA para Desenvolvimento de Software
author  : Agent Orchestrator Team <team@praxis-orchestrator.dev>
home    : https://github.com/luhfilho/praxis-orchestrator
```

An AI agent orchestrator for software development, at 2.0.0, holding the name
this repository's `pyproject.toml` declares. This project has never published:
`release-please.yml` builds a wheel and attaches it to a GitHub Release, and
nothing in CI touches PyPI. So `pip install praxis-orchestrator` today installs
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
| Python package `praxis_orchestrator` → `praxis_orchestrator` | 2066 | 344 |
| Distribution/repo name `praxis-orchestrator` → `praxis-orchestrator` | 756 | 101 |
| `Praxis Orchestrator` in titles, docs and the OpenAPI description | 24 | 22 |

The package rename is the largest number and the lowest risk: the project has
never been published, so **no external importer exists**. There is no
deprecation shim to write, because there is nobody to deprecate for.

### Group B — per-attempt runtime variables (mechanical, but read the note)

Six `ORCHESTRATOR_*` variables are injected by the runner into each agent
subprocess and live only for that attempt:

`ORCHESTRATOR_ATTEMPT_ID` (78), `ORCHESTRATOR_ATTEMPT_NUMBER` (56),
`ORCHESTRATOR_PLAN_ID` (48), `ORCHESTRATOR_GOAL_ID` (48),
`ORCHESTRATOR_TASK_ID` (47), `ORCHESTRATOR_RUN_ID` (47)

Nothing persists them, so renaming to `PRAXIS_*` needs no migration. **The
note:** they are part of the contract with an agent runtime, so any prompt,
skill file or wrapper script that reads them renames in the same commit. Grep
`fixtures/`, `demos/` and `.orchestrator/` before assuming the package is the
only reader — done, and the package plus one test and one architecture doc were
the only readers.

**Two entries in the original list were miscounted, and both are corrected
here rather than silently dropped:**

- **`ORCHESTRATOR_SCOPE` (43) is not an environment variable.** It is
  `SqliteConfigStore.ORCHESTRATOR_SCOPE`, the Python constant holding the
  config *scope* name `"orchestrator"` — a value **persisted in the `config`
  table** on every existing install. Renaming it is a data migration for zero
  user-visible gain, so it stays. The count was picking up a class attribute
  that merely shares the prefix.
- **`ORCHESTRATOR_PROJECT_ID` (2) does not exist.** The two occurrences were
  this document's own. Nothing injects or reads it.

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

### Group D — every trace of `praxis`, which is a third party's mark

**The original working name is a third party's mark.** That reframes this group
entirely. It was not a findability problem to be tidied up alongside the package
rename; it was somebody else's trademark sitting in a repository about to be
launched publicly, and it came out **completely**.

47 tracked files. The string has spread further than the guest name suggests:

| form | count | where |
|---|---|---|
| `praxis-dev` | 39 | libvirt domain, hostname, `infra/dev-vm/`, CI, docs |
| `Praxis Orchestrator` | 25 | titles, OpenAPI description, CLI help, README |
| `praxis` | 20 | prose across docs |
| `praxis-acceptance-*` | 18+ | container name prefix, and its tests |
| *(as an npm name)* | 3 | **the npm package name** in `frontend/package.json` — now `praxis-console` |
| `praxis-userns` | 5 | the guest's user-namespace config |
| `praxis-probe` | 4 | dev-VM verification |
| `praxis-vite-cache`, `praxis-playwright*`, `praxis-cycle-e*` | 4 | frontend build/e2e artefacts |

Renaming the guest means a **rebuild**, not an edit:
`make -C infra/dev-vm destroy && make up`, then re-seed the six-agent roster with
`seed-agents.py` and confirm `make verify` still returns 8 of 8.

The container prefix lives in `infra/environment/container_environment.py` and is
asserted in `tests/integration/test_container_environment.py` — which Phase 10A
changed to snapshot-and-diff, so the prefix is now read from one place instead of
matched as a literal in two.

#### Done 2026-08-11

All 49 files carrying the mark were rewritten in one pass: `praxis-dev`,
`praxis-acceptance-*`, `praxis-userns`, `praxis-probe`, the build-cache and e2e
artefact names, and `Praxis Orchestrator` for the standalone display uses. The
npm package became `praxis-console` (it was named for the old mark plus a
legacy word; `console` is what the frontend is called everywhere else).

`tests/unit/test_no_third_party_marks.py` scans the **whole working tree** — not
a curated subset, because the mark had already escaped into a package name, a
container prefix and a build-cache directory before anyone counted. Its
`xfail` ratchet is gone; it is now a plain passing guard.

**Still outstanding, and it cannot be done from inside the guest:** the running
VM still answers `hostname` as the old name. The configuration is renamed
(`VM_NAME`, `local-hostname`, `create-vm.sh`, the Makefile), but the live domain
keeps its identity until it is rebuilt **from the host**:
`make -C infra/dev-vm destroy && make -C infra/dev-vm up`, then re-seed the
roster with `seed-agents.py` and confirm `make verify` returns 8 of 8.

#### The decision taken on history and evidence

1. **Git history — the mark stays there.** Decided 2026-08-11: out of the
   working tree, left in history. Scrubbing it means `git filter-repo` and a
   force-push, which breaks every clone, rots every existing PR and commit link,
   and rewrites the audit trail this project's discipline rests on — a
   disproportionate remedy for a string in the commit log of a project that was
   never published.

2. **Recorded run evidence — substituted, and said so here.**
   `demos/static-site-v1/runs/*/worker-log.txt` and
   `.orchestrator/runtime-runs/*/logs/*.out` contained the old hostname and
   container names because that is genuinely what those runs saw. They were
   rewritten in the same pass, so **the hostnames and `*-acceptance-*` container
   names in those files are substitutions, not the literal strings the run
   emitted.** Nothing else in them was touched: timings, exit codes, commit SHAs
   and command lines are as recorded.

   This is the one place the rename knowingly edits a record, and it is written
   down rather than left for a reader to discover. The cleaner fix is to re-run
   `static-site-v1` and publish that instead — ~13 minutes, ~$0.013, and it
   refreshes the launch numbers at the same time. Recommended before the demo is
   used as launch proof.

   **Correction, 2026-08-15: that re-run does NOT need the guest rebuilt, and
   this section previously implied it did.** The phrase "the old hostname" is
   wrong about the mechanism. What the demo logs actually carried was
   `acceptance.container_started container=<mark>-acceptance-…` — the container
   **name prefix**, built in `infra/environment/container_environment.py` from
   the old product name **in code**, not read from the machine. It is now
   `praxis-acceptance-`, and nothing in the package reads the host's name at
   all: `socket.gethostname`, `platform.node` and `uname` have zero occurrences
   in `praxis_orchestrator/`. A run on today's guest therefore emits no third
   party's mark, and `test_no_third_party_marks.py` would pass on its output.

   The guest rebuild is still worth doing for its own sake — the live domain
   keeps the old identity until it happens — but it blocks nothing.

---

## Migration design for Group C

**Superseded 2026-08-15 by a clean break** (decision 65). The design below —
read-both-prefer-new environment aliases, adopt-in-place for the state
directory, and two CLI entry points — was built, shipped, and then deleted the
same day by owner decision. It is kept here because the reasoning that replaced
it only makes sense against it.

### What was built, and why it went

The layer assumed a population that does not exist. The project has never
published to any index; `release-please` attaches a wheel to a GitHub Release
and nothing else. Every install predating the rename is the maintainer's own,
and they are countable on one hand. So the layer bought nothing and cost a
permanent second code path — two spellings of every variable, two entry points,
a four-branch home resolver — that everyone touching configuration afterwards
would have to read and reason about, forever, on behalf of nobody.

It also produced a defect of exactly the shape it was meant to prevent, which is
the strongest argument against it: `/api/readiness` read the master key from
`os.environ` rather than through the alias, and so reported a **working** install
as broken, with a detail line inviting the operator to generate a second master
key — which permanently orphans the encrypted store. An alias is only as good as
the number of places that use it, and the one site that skipped it was the site
whose whole job is telling an operator whether their install works.

### What replaced it

**An actual migration of the one machine that had state**, performed rather than
documented:

| before | after |
|---|---|
| `~/.orchestrator` | `~/.praxis` (moved, with `~/.orchestrator.bak-20260815` kept) |
| `~/.orchestrator-env` | `~/.praxis-env`, mode 600, same key under `PRAXIS_MASTER_KEY` |
| a `/etc/profile.d` script named for the old mark, exporting `ORCHESTRATOR_HOME` | `/etc/profile.d/praxis.sh` exporting `PRAXIS_HOME` |

Verified, not assumed: both stored provider keys decrypt from the moved database
under the new variable, and `praxis plan list` returns the two demo plans.

The code that remains is `infra/state_home.py` — four lines that answer
`PRAXIS_HOME` else `~/.praxis`, with no fallback. `tests/unit/test_state_home.py`
asserts that each pre-rename variable is **ignored**, and that `praxis` is the
only declared entry point: a half-removed alias would be worse than either
having one or not.

`orchestrator.db` keeps its filename inside the directory. It describes what it
is, carries no stale brand, and renaming it would be churn with a migration step
attached.

---

## Sequencing

1. Register the name on PyPI, npm, and GitHub, and the two domains. Registering
   before renaming costs nothing and removes the risk of losing the name
   mid-migration. **Still outstanding** — the rename went first, and nothing in
   CI publishes today, so nothing breaks until someone tries.
2. **Group D first, not last.** It was sequenced last while it was a branding
   chore; as a third party's mark it is the item with a deadline, and it is
   independent of the name that replaces it. Rebuild the guest, re-seed the
   roster, re-run `make -C infra/dev-vm verify` for its 8 of 8, and decide the
   two history/evidence questions above.
3. Group A + B: package, distribution, titles, per-attempt vars. Large diff,
   mechanical, `mypy` and the suite are the check.
4. Group C, with its regression tests. Small diff, all the risk.
5. Docs, fixtures, demos and README in the same PR as the code that moved —
   per the repo's own docs-discipline rule, and `test_documented_paths_exist.py`
   (now covering source comments too) will fail the build if any are missed.

---

## Executed

**Group D — 2026-08-11.** 49 files; recorded above.

**Groups A, B and C — 2026-08-15.** All on `phase-10b-rename-package`.

| what | outcome |
|---|---|
| Python package `agent_orchestrator` → `praxis_orchestrator` | 344 files; zero occurrences of the old name remain in tracked files |
| Distribution `praxis-orchestrator`, titles, OpenAPI description | done; `openapi.json` and `src/types/generated/` regenerated from the renamed source |
| **GitHub repository** → `guilhermeUpToTask/praxis-orchestrator` | every link in the tree rewritten to match; GitHub redirects the old URL |
| Per-attempt runtime variables → `PRAXIS_*` (Group B) | `infra/runtime/cli_runner.py`, its taxonomy test, `docs/architecture/execution-model.md` |
| `PRAXIS_HOME` / `PRAXIS_MASTER_KEY` / `PRAXIS_API_TOKEN` / `PRAXIS_DB_URL` | read directly from `os.environ` at the four sites that read the environment at all — no alias |
| State directory `~/.praxis` | `infra/state_home.py`; the 15 fixture scripts use the same one-line rule |
| CLI `praxis` | the only entry point in `[project.scripts]` |
| The maintainer's machine | migrated: state directory moved (backup kept), env file reissued, `/etc/profile.d` rewritten, provider keys proven to still decrypt |
| Regression tests | `tests/unit/test_state_home.py` — home resolution, container agreement, each pre-rename variable **ignored**, `praxis` the only entry point |

### What the execution found that the scope did not predict

The first three below are findings **about the compatibility layer**, which no
longer exists. They are kept because together they are the argument for deleting
it, and because two of them are mistakes worth not repeating.

1. **`/api/readiness` reported a working install as broken.**
   `routers/readiness.py` read the master key from `os.environ` directly rather
   than through the alias, so an install whose key was under the old name got
   `fail: PRAXIS_MASTER_KEY is not set` while the secret store read it fine.
   The harm is not the red mark, it is the instruction attached: the operator's
   next move is to generate a master key, and a second key **permanently orphans
   the encrypted store**. Reproduced first, then fixed.
2. **Twenty-one tests changed meaning without failing.** They clear `PRAXIS_*`
   to assert the no-key / no-token state, and with the alias in place the value
   arrived through the old name instead. Two failed outright on the dev guest;
   the other nineteen passed for reasons unrelated to what they claim to test.
3. **`ORCHESTRATOR_EMBED_COORDINATORS` got an alias it should not have had.**
   It belongs to the pre-refactor architecture and nothing has read it for two
   rewrites — an alias for a variable no code reads is a promise that cannot be
   tested.
4. **`orchestrator.db` keeps its name.** It describes what it is, carries no
   stale brand, and renaming it would be churn with a migration step attached.
   The filename is internal; the directory is what an operator types.

---

## Closed item — the name

**`praxis-orchestrator` is final.** Settled by the owner on 2026-08-11 and
reaffirmed 2026-08-15. The `Praxis Framework` collision an earlier draft of this
section raised was put to the owner, considered, and accepted: a programme and
project management methodology is not an AI agent orchestrator, and the two are
not confusable in the market either uses.

This section previously told the reader to treat the name as *provisional* until
a registry search returned clean. **That instruction is withdrawn.** It survived
past the decision that answered it and turned a closed question into a caveat
that reattached itself to the name every time it appeared downstream. Nothing in
this repository treats the name as conditional.

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
