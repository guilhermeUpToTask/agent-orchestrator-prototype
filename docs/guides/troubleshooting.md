# Troubleshooting

Real failures, with the signature you actually see and the fix. Every entry here
was hit during development or a live run — none is hypothetical.

Start with the readiness checklist: **Settings → Readiness**, or

```bash
curl -s localhost:8000/api/readiness | jq '{ok, checks: [.checks[] | {name, status, detail}]}'
```

---

## Nothing happens after I submit a brief

The plan is accepted, the status says `running`, and then it sits there.

**Almost always: no worker is running.** Leases prove a worker is *busy*; an idle
one holds none, so before the first claim "running, nothing to do" and "never
started" look identical over HTTP. One read separates them:

```bash
curl -s localhost:8000/api/workers | jq '[.[] | {worker_id, mode, stale, seconds_since_seen}]'
```

- `[]` — no worker has ever reported. Start one, or use `praxis serve`,
  which supervises one for you.
- `stale: true` — a worker reported and went quiet (crashed, or the machine
  slept). Restart it.

Second possibility: the plan is **waiting on you**. Check `pending_gate` — an
intent or cycle-draft gate blocks all progress until approved by exact revision.

---

## `sqlite3.OperationalError: no such column: …`

The database is on an older revision than the code. Migrations are not applied
automatically by `api start` or `worker start`.

```bash
praxis db upgrade
```

`praxis serve` migrates on startup, so this failure mostly bites the
two-process development path after a pull.

---

## `ImportError: Can't find Python file .../site-packages/alembic/env.py`

An older installed version whose wheel did not carry its migration scripts.
Upgrade; the wheel now ships them inside the package and the release refuses to
publish one that does not.

---

## The UI loads but nothing in it works

Every request fails in the browser console with `ERR_CONNECTION_REFUSED`, while
`curl localhost:8000/health` is fine.

An older build hardcoded the API base to `http://localhost:8000`, so a console
served on any other port called the wrong one. Fixed: the packaged UI now uses
its own origin. If you see this on a current version, check you are not running
a stale `frontend/dist` behind your own web server.

---

## `catalog: fail — 0 capabilities · 0 agents · 0 provider/model`

A fresh install has an empty catalog. Go to **Settings → Get started**, which
sequences the setup and, on Tier 0, never asks for an API key.

Note that this check demands provider and model rows even on Tier 0, where
neither the stub reasoner nor the dry-run runner resolves one. You can satisfy
it with `praxis seed demo --stub`, or ignore `catalog` for a Tier 0 run —
the wizard will tell you what actually matters.

---

## `NO_DEFAULT_AGENT: No default agent is configured to fall back to`

Hit when editing a task's capabilities, or repairing a contract, on an install
with no default agent. Tasks bind to the first agent whose capabilities cover
their requirements; anything uncovered falls back to the default, and without one
the edit is refused.

**Settings → Agents → Set default**, or the wizard's default-agent step.

---

## The run stalls with `provider_waiting`

Not a failure. A rate limit, quota or connection problem opens a circuit and the
work waits on automatic backoff, bounded by wall-clock ceilings (6h ordinary,
26h for a daily quota) rather than an attempt count.

```bash
curl -s localhost:8000/api/plans/$PLAN | jq '.provider_waiting'
```

`needs_attention: false` means the orchestrator owns the recovery — free models
throttle constantly and runs still complete. Only past the ceiling does it
escalate to a block. On the Overview screen this appears under **Recovering
automatically**, deliberately separate from **Needs attention**.

---

## An attempt failed with `verification_error` and the task retried

Working as designed. Agent output is a **candidate**: the orchestrator re-runs
the task's verification commands and checks scope, protected test hashes and
branch integrity before promoting anything. A rejected candidate is not
terminal — the retry carries the rejection reasons into the next prompt.

Seen in a live run: `succeeded → verification_error → succeeded`, where the
model's first implementation failed the test it had itself authored.

---

## `AUTH_ERROR` on the first real attempt

The agent is bound to a provider/model it cannot use: no key, an unreadable key,
or a row that was deleted. This is terminal by design — retrying a broken
binding just burns the budget.

```bash
curl -s localhost:8000/api/runner/status | jq '{mode, valid, agents: [.agents[] | {agent_name, valid, detail}]}'
```

Also check `PRAXIS_MASTER_KEY` is set in the environment of the **worker**
process, not only the API — they are separate processes and each reads its own.

---

## `PROJECT_BINDING_INVALID: scp-style git remotes are not supported`

`git@github.com:you/repo.git` is not supported. Use the equivalent form:

```
ssh://git@github.com/you/repo.git
https://github.com/you/repo.git
```

A plain local path (`/home/you/code/project`) also works and is the fastest way
to try the tool.

---

## The plan ran but my repository is untouched

Check the project's `repo_url`. A project created **without** one gets a scratch
repository under `$PRAXIS_HOME/projects/<id>/repo`, and the run happily
succeeds against a tree you never look at.

```bash
curl -s localhost:8000/api/projects | jq '[.[] | {id, name, repo_url}]'
curl -s localhost:8000/api/projects/$PROJECT/readiness | jq
```

`PROJECT_REPO_DIR` is read by nothing — a test enforces that. The binding is the
project row.

Also expected: your **default branch is never written**. Work lands on
`cycle/<id>`, promoted from `goal/<id>`, promoted from `task/<id>/<run_id>`. Look
for the cycle branch, not for changes on `main`.

---

## A config change had no effect

`reasoner.*` keys used to be read once at worker boot, so a successful write was
silently ignored until a restart. Fixed — they are re-resolved per planning
call. `agent_runner.*` resolves per task per run and was always live.

If you are on an older version, restart the worker after changing `reasoner.*`.

---

## Mixed tiers

A real reasoner with a dry-run runner (or the reverse) produces evidence that
means nothing: a real plan verified by a dummy, or a dry-run plan spending
tokens. Keep the halves together:

| Tier | `reasoner.mode` | `agent_runner.mode` |
|---|---|---|
| 0 | `stub` | `dry-run` |
| 1 | `llm` | `real` |

`fixtures/first-cycle-v1/scripts/preflight.sh` fails a mixed pair on purpose.

---

## Deleting a plan is refused with 409 `PLAN_BUSY`

A worker holds a live lease. Wait for the current action to finish, or stop the
worker. Deletion cascades to cycles, attempts, evidence, chat and telemetry —
export anything you want to keep first (see
[evidence.md](evidence.md)).
