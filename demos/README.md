# Demos — what the orchestrator produces, shown rather than claimed

A demo is **one realistic run on real models, captured**. It exists to be looked
at. It is the artifact an invitation points at.

## Demos are not fixtures. Do not merge the two.

[`fixtures/`](../fixtures/) and `demos/` look similar and have opposite jobs.
Confusing them is the mistake this file exists to prevent — someone will
otherwise try to make a demo deterministic, fail, and conclude something is
broken.

| | [`fixtures/`](../fixtures/) | `demos/` |
|---|---|---|
| **Job** | catch regressions | show what the system produces |
| **Runs** | repeatedly, from a clean state | once, then captured |
| **Reasoner** | stub (Tier 0) or pinned (Tier 1) | real, on real models |
| **Determinism** | required — same result every time | **impossible**, and that is fine |
| **Assertions** | exact (16/16 named expectations) | structural only (see below) |
| **CI** | some are test-locked and gate the build | never |
| **A red run** | a bug, fix it | evidence, keep it |

The reason a demo cannot be a fixture is simple: **a real reasoner decomposes
the same brief differently every run.** One run yields three goals, the next
four. A fixture that asserted "three goals" would fail on a system that is
working correctly, which is worse than asserting nothing.

## What a demo still owes you

Being un-lockable is not permission to be unchecked. "Don't trust, verify" is
this project's entire argument, and a demo whose output nobody validates would
be exactly the thing it criticizes.

Every demo therefore ships a checker asserting the properties that hold **no
matter how the reasoner decomposed the work**:

1. every goal in the cycle was promoted;
2. every commit SHA the API served actually resolves in git;
3. the default branch is byte-identical to the seed tag;
4. no goal was merged without accepted, revision-bound evidence;
5. the disposition was recorded with an output reference;
6. the root plan returned to `idle`.

Plus, where the project shape allows it, an **acceptance check on the actual
output** — see below.

## Why the first demo generates files

`static-site-v1` takes markdown in and writes HTML out. That is not an
accident of taste.

The orchestrator can currently prove *a command exited 0 against this commit*.
It cannot yet prove *the application works* — that is the cycle acceptance run,
whose `DockerEnvironment` adapter is blocked on an environment that can run
containers (ROADMAP P8.5). Until that lands, a project whose goals end in
"tests passed, nobody can tell whether it runs" would showcase precisely the
gap Phase 8 exists to close.

A files-in/files-out generator has no such gap. The demo ends with a real
`index.html` you open in a browser. **A human confirms the product works with
no container, no acceptance run, and no trust in the evidence document** — and
then the evidence document tells them *why* it works, commit by commit.

## Honesty rules

- **A red run is published, not retried.** If a demo fails, the captured run
  and the reason ship as-is. Every walkthrough in this repository has found real
  defects; a demo curated until it looks good stops finding them and starts
  lying.
- **The pin is recorded**: reasoner provider/model, agent runtime, orchestrator
  version, and cost. A result nobody can situate is an anecdote.
- **No manual repair mid-run.** If a human fixes the code, the run is over and
  the artifact says so.

## Running one

Each demo has its own `README.md` with the exact commands. They are API-only
(`curl` + `jq`), reuse `scripts/api.sh` and `scripts/capture-run.sh` from the
fixtures, and require Tier 1 (`reasoner.mode=llm`, `agent_runner.mode=real`).
