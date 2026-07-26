# Planning recovery v1 — does a retry start better informed?

`happy-path-v1` proves the orchestrator can finish a small job. It cannot prove
anything about **recovery**, because on a green run nothing ever fails. This
fixture exists for the opposite case: it makes enrichment fail on purpose and
then asks whether the next attempt is any better off than the first.

|  |  |
|---|---|
| **Proves** | a failed planning attempt leaves evidence, and the retry is granted more room because of it |
| **Target repo** | the `happy-path-v1` seed (unchanged — this fixture is about the orchestrator, not the product code) |
| **Interface** | `GET/POST/PUT /api/…` via `curl` + `jq` only |
| **Cost** | free-tier reasoner; no agent runs at all (it never reaches execution) |

## Why config, not a brief

A fixture that hoped the model would produce a bad contract would be measuring
the model, and every run would differ. Instead this drives the failure through
`reasoner.max_turns`: set it to 1 and **every** enrichment session dies on its
turn budget before it can submit, on any model. That is the exact failure
observed live — a session spends its whole budget and submits nothing.

`reasoner.max_turns` bounds EVERY reasoner session, so the stage that starves is
the first one after the intent gate: **cycle architecture**, before a draft or a
goal exists. The stage is incidental — the question is whether a dead planning
session leaves anything behind at all.

⚠️ The budget is read ONCE at worker boot (`AppContainer.reasoner` is a
`cached_property`), so setting the key mid-run is accepted, echoed back by
`GET /api/reasoner/status`, and silently ignored by the running worker. This
fixture found that; see the ROADMAP deferral. The script therefore VERIFIES the
precondition instead of setting it, and tells you to restart the worker.

## Run it

Requires `curl` and `jq`. From the repo root, with the API already running
(`backend/scripts/dev.sh start`) and a real reasoner seeded:

```bash
export HAPPY_PATH_API=http://127.0.0.1:8000
./fixtures/planning-recovery-v1/scripts/run.sh
```

The script restores `reasoner.max_turns` to its original value on exit, including
on failure — leaving a plan's reasoner starved would poison every later run
against the same home.

## What it asserts

1. **Evidence is written.** After the first starved attempt,
   `GET /api/plans/{id}/planning-artifacts?purpose=cycle_architecture` is
   non-empty. Before this existed, the attempt left nothing: the retry rebuilt
   its prompt from scratch and died identically, forever.
2. **Attempts accumulate rather than overwrite.** The `sequence` climbs. The
   `planning_operations` row is deliberately REUSED across an outage, so a design
   that stored this on that row would have kept only the last attempt.
3. **`abandoned` never becomes advice.** A session that submitted nothing has no
   rejection to teach, so its `rejection_reasons` stay empty and it is never
   replayed into a prompt — it only buys budget.
4. **The turn cost is recorded** on every attempt — that number is the evidence
   the escalating budget reads to grant a retry more room.
5. **The operator reset works.** `DELETE .../planning-artifacts` clears the
   memory, for when a bad draft keeps steering the retry.

The recovery half — restore the budget, restart the worker, `retry-stage`, watch
it pass — is printed as manual follow-up rather than asserted, because the
restart cannot be driven through the API.

## What it deliberately does NOT cover

- Execution, verification, promotion, publication — `happy-path-v1` owns those.
- The *rejected-with-payload* replay path. Forcing a model to submit a
  specifically bad contract is not deterministic, so that path is covered by
  tests (`tests/unit/reasoner/test_openai_reasoner.py`) rather than here.
- Multi-goal fan-out.

## Findings go where the code is

A defect this fixture exposes follows the same rule as `happy-path-v1`:
reproduction → minimal fix → regression test against the fake AND SQLite truth →
rerun. Do not loosen an assertion here to make a run pass.
