# Tier 1 — real models

Tier 0 proves the *lifecycle*. Tier 1 proves the *product*: a real model plans,
a real CLI writes code, and the orchestrator verifies the result before
promoting it.

**Read [SECURITY.md](../../SECURITY.md) first.** In Tier 1 the agent CLI runs
unsandboxed, as your user, against the repository you point it at.

## What it costs

The orchestrator does **not** enforce a spend cap. It bounds *waiting* (wall
clock ceilings on capacity outages) and *retries* (a per-task budget, plus a
per-kind ceiling), but nothing stops a long cycle from spending real quota.

Two controls that work:

- **Free models.** A cycle on a `:free` model costs nothing but patience. Live
  runs of `fixtures/first-cycle-v1` completed this way, absorbing repeated rate
  limits without intervention.
- **A provider-side budget limit.** Set one. It is the only hard stop.

Cost scales with cycle size, not wall time: goals × tasks × attempts, plus one
planning session per goal. Keep first cycles to one goal.

## Setup

**Settings → Get started**, tier **Tier 1 — real models**. Seven steps, in
dependency order:

1. **Register a provider** — name, base URL, API key. The key is
   envelope-encrypted at rest, which requires `PRAXIS_MASTER_KEY` in the
   environment of **both** the API and the worker (separate processes, each
   reads its own).

   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

2. **Add a model** — named exactly as the provider names it.
3. **Create an agent bound to that model** — and install the CLI it names
   (`pi`, `claude`). Unbound, its first attempt fails with a terminal
   `AUTH_ERROR` mid-run rather than at setup.
4. **Set the default agent.**
5. **Point the reasoner at your model** — the wizard writes provider and model
   before mode, so the config is never briefly `llm` pointing at nothing.
6. **Switch the agent runtime to `real`.**
7. **Create a project** bound to a real repository.

Then confirm the machine agrees:

```bash
curl -s localhost:8000/api/readiness | jq '{ok, checks: [.checks[] | {name, status, detail}]}'
curl -s localhost:8000/api/runner/status | jq '{mode, valid, binaries: [.binaries[] | {name, ok}]}'
```

A missing binary matters only if an agent is **bound** to it — an install with
three `pi` agents will still report `gemini` missing, and that is not a problem.

## Never mix tiers

| Tier | `reasoner.mode` | `agent_runner.mode` |
|---|---|---|
| 0 | `stub` | `dry-run` |
| 1 | `llm` | `real` |

A real reasoner verified by a dummy runner, or a dry-run plan spending tokens,
produces evidence that means nothing.

## What a real run looks like

Slower and noisier than Tier 0, and that is normal.

- **Rate limits are expected.** A free model throttles constantly. The
  orchestrator opens a circuit and waits on automatic backoff, bounded by
  wall-clock ceilings (6h ordinary, 26h daily quota) rather than an attempt
  count. This appears under **Recovering automatically**, not **Needs
  attention**. Observed attempt sequences from live runs:
  `rate_limit → succeeded → rate_limit → succeeded`.
- **Rejected candidates are expected.** The orchestrator re-runs the task's
  verification commands and checks scope, protected test hashes and branch
  integrity. A candidate that fails is discarded and the retry carries the
  rejection reasons into the next prompt. Observed:
  `succeeded → verification_error → succeeded`, where the model's first
  implementation failed a test it had itself written.
- **Planning takes turns.** Discovery may ask a question before committing.

Watch it with `ConsoleDock` (live attempt logs, resumable) or:

```bash
curl -s localhost:8000/api/plans/$PLAN/attempts \
  | jq '[.tasks[].runs[].attempts[]? | {number, status, failure_kind}]'
```

## Pinning a run so two runs are comparable

Change **one variable per run series**. Record, for every run:

- the exact brief and fixture version
- the orchestrator version or commit
- the reasoner provider/model **and** the agent runtime/provider/model
- plan, cycle, goal, task, run and attempt ids

`fixtures/first-cycle-v1/scripts/run-cycle.sh` prints the identifiers, and
`verify_run.py --tier 1` adds the check a dry run cannot pass: that the
implementation was actually written on the promoted tree.

## When it stops

- `provider_waiting` with `needs_attention: false` — automatic, leave it.
- A block with `requires_human: true` — read `explanation` and
  `legal_resolutions`; the Overview offers exactly the actions the block
  advertises.
- `AUTH_ERROR` — a broken binding, terminal by design. See
  [troubleshooting.md](troubleshooting.md).
