# Getting started

From nothing to a completed cycle. Tier 0 — free, deterministic, no API key.

You need **Python 3.11+** and **Git**. Nothing else.

## 1. Install and start

```bash
pipx install agent-orchestrator     # or: uvx --from agent-orchestrator orchestrate
orchestrate serve
```

```
✓  state directory /home/you/.orchestrator
✓  worker worker-1 started (pid 12345)
✓  http://127.0.0.1:8000
```

One command does four things: migrates the state directory, starts the API,
supervises a worker as its own process, and serves the console — the wheel
carries the built UI, so there is no second install and no second port.

State lives in `ORCHESTRATOR_HOME` (default `~/.orchestrator`): your plans,
evidence and encrypted keys. Point it elsewhere with the environment variable if
you want a throwaway install:

```bash
ORCHESTRATOR_HOME=/tmp/try-orchestrator orchestrate serve --port 8100
```

## 2. Set it up

Open <http://127.0.0.1:8000> and go to **Settings → Get started**.

The wizard sequences setup in the order the pieces depend on each other, and
shows progress as `n / total`. Leave the tier on **Tier 0 — free, no API key**
and it opens at 2/5, because stub planning and the dry-run runtime are already
the defaults. Three things remain:

1. **Create an agent** — tasks bind to an agent; dry-run needs no provider.
2. **Set the default agent** — the fallback for any task no capability covers.
3. **Create a project** — name it, and give it the **path to a Git repository
   you can afford to lose**. Leave the path blank and you get a scratch repo
   instead, which is fine for a first look but means the run touches nothing of
   yours.

Every step is also reachable from its own settings section; the wizard just
orders them and does the minimum write for each.

## 3. Run a cycle

From the **Plans** screen, *Open project plan*, pick your project, and describe
what you want built. Keep the first one small — one goal, one or two tasks.

What happens next, and where you appear in it:

| Stage | Who acts |
|---|---|
| **Intent** | You converse. The reasoner normalizes the brief into a proposal. |
| **Intent gate** | **You approve** the exact revision. |
| **Architecture** | The reasoner drafts an ordered goal roadmap. |
| **Draft gate** | **You approve** — approval activates the cycle. |
| **Enrichment** | Just-in-time, head goal only: a frozen contract with criteria, scope, commands and one verification mode. |
| **Execution** | The worker runs each task; output is verified before anything is promoted. |
| **Publication gate** | **You choose** the disposition. |

On Tier 0 the reasoner follows a deterministic grammar rather than a model, so
discovery may ask a clarifying question before it commits. Answer it and it
proceeds.

Watch progress on **Overview**: current operation, worker lease, attempts, and
two separate queues — **Needs attention** (you) versus **Recovering
automatically** (not you).

## 4. Find the result

Your default branch is untouched — that is a guarantee, not a coincidence. Work
lands on `cycle/<cycle_id>`, promoted from `goal/<goal_id>`, promoted from
`task/<task_id>/<run_id>`, and only independently verified work moves up a level.

```bash
git -C /path/to/your/repo branch -a
git -C /path/to/your/repo log --oneline cycle/<cycle_id>
```

At the publication gate you record a disposition: `retain_branch` (keep the
branch and open a PR yourself), `merge`, `open_pr`, or `discard`. The
orchestrator has **no authenticated forge integration** — `merge` and `open_pr`
record a reference to an operation *you* performed. It never pushes or merges on
your behalf.

The evidence behind the run — the exact command, its exit code, the candidate
and test commits, the promotion SHAs — is on the cycle:
see [evidence.md](evidence.md).

## Prefer the terminal?

`fixtures/first-cycle-v1/` is the same walkthrough over the API only, in one
command, with a checker that verifies the result rather than trusting it:

```bash
./fixtures/first-cycle-v1/scripts/materialize.sh    # a disposable target repo
./fixtures/first-cycle-v1/scripts/run-cycle.sh      # the whole cycle
python3 fixtures/first-cycle-v1/scripts/verify_run.py --plan "$PLAN" --repo "$REPO"
```

## Next

- [tier-1.md](tier-1.md) — real models writing real code, and what it costs
- [troubleshooting.md](troubleshooting.md) — real failures and their fixes
- [../../SECURITY.md](../../SECURITY.md) — **read before pointing it at anything
  you care about**: agent runtimes are unsandboxed
