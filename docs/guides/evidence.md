# Finding and sharing evidence

What the orchestrator can prove about a run, where it lives, and how to hand it
to somebody else.

The claim worth checking is never "the agent said it worked". It is: *this exact
command exited 0 against this commit, and this branch merged into that one at
this SHA*.

## One read per cycle

```bash
curl -s localhost:8000/api/plans/$PLAN/cycles/$CYCLE/evidence | jq
```

```json
{
  "cycle_status": "completed",
  "disposition": { "disposition": "retain_branch", "output_reference": "cycle/51e2…" },
  "goals": [
    {
      "goal_id": "e583…",
      "promotion": { "from_ref": "goal/e583…", "into_ref": "cycle/51e2…", "merge_sha": "2e872ff…" },
      "tasks": [
        {
          "task_id": "6e4a…",
          "accepted_evidence": [
            {
              "exact_command": "python -m pytest -q tests/test_slug.py",
              "exit_code": 0,
              "candidate_commit_sha": "7632ab7…",
              "test_commit_sha": "…"
            }
          ],
          "rejected_evidence_count": 1,
          "protected_scope": { "allowed_scope": ["src/…"], "forbidden_scope": [] }
        }
      ]
    }
  ],
  "delivery": {
    "binding": "local",
    "repository_path": "/home/you/projects/widgets",
    "default_branch": "main",
    "cycle_branch": "cycle/51e2…",
    "in_operator_checkout": true
  },
  "unattributed_evidence_refs": []
}
```

Read it as five claims:

- **`accepted_evidence`** — the exact command, its exit code, and the commits it
  ran against. Revision-bound: evidence attached to a superseded contract
  revision is not served as accepted.
- **`rejected_evidence_count`** — candidates the orchestrator threw away. A
  non-zero count is a healthy sign, not a problem.
- **`protected_scope`** — what the task was allowed to touch, and the test
  hashes it was forbidden to weaken.
- **`promotion`** — where the code went, recorded when the merge happened rather
  than reconstructed later from a branch-naming convention.
- **`disposition`** — what you decided at the publication gate.

## Where your code actually is

`delivery` answers the question the rest of the document assumes you already
know: *which repository on this machine holds `cycle/<id>`?* It has three
answers, decided by the `repo_url` you gave the project, and only one of them
means the work is already in a checkout you use.

| `binding` | `repository_path` | What to do |
|---|---|---|
| `local` | the repository you named | Nothing to transport. `git -C "$PATH" diff "$BASE".."$BRANCH"` |
| `remote` | a clone the orchestrator owns, under `$ORCHESTRATOR_HOME/projects/<id>/repos/<hash>` | The branch is **not** in your checkout. Add the clone as a remote and fetch |
| `scratch` | an auto-seeded demo repository | The run demonstrated the flow; there is no code here you want |

For a `remote` binding — `in_operator_checkout` is `false` — the work reaches
your own repository like this:

```bash
git remote add orchestrator "$PATH"
git fetch orchestrator "$BRANCH"
```

`default_branch` is probed on disk rather than assumed, so the diff command it
feeds resolves even when the project's trunk is not called `main`. It is `null`
only when the repository is unreadable — moved or deleted since the run — in
which case `repository_path` still tells you where the work was written.

The console renders whichever of the three applies, with the commands filled
in, under **Output** in the cycle evidence summary.

## Verifying it yourself

Never take the merge SHA on faith — ask git:

```bash
git -C "$REPO" cat-file -t 2e872ff              # commit
git -C "$REPO" rev-list --parents -n 1 2e872ff  # two parents = a real --no-ff merge
git -C "$REPO" rev-parse main                   # unchanged, equal to the seed
```

The fixture checker does exactly this and refuses a run whose served SHA git does
not have, or whose "promotion" is a fast-forward rather than a merge:

```bash
python3 fixtures/first-cycle-v1/scripts/verify_run.py \
  --plan "$PLAN" --repo "$REPO" --tier 1
```

## The rest of the trail

| What | Where |
|---|---|
| Attempt timeline | `GET /api/plans/{id}/attempts` |
| One attempt's captured log | `GET /api/plans/{id}/attempts/{attempt}/log` |
| Live log of a running attempt | `GET …/log/stream` (SSE, resumable by byte offset) |
| Fine-grained agent telemetry | `GET /api/plans/{id}/agent-events` |
| Domain event feed | `GET /api/events` (SSE, at-least-once — dedup on `event_id`) |
| Planning retries and what they produced | `GET /api/plans/{id}/planning-artifacts` |

In the console: **ConsoleDock** for attempt logs, **Overview** for the cycle
evidence summary.

## Sharing a run

```bash
# the full report: plan, planning operations, runs, attempts, telemetry, events
python backend/scripts/export_plan_runs.py --plan-id "$PLAN" --pretty --output run.json

# a focused snapshot of one plan's current debugging state
python backend/scripts/snapshot_current_plan.py --plan-id "$PLAN" --pretty --output snapshot.json

# a sanitized bundle instead of one file
python backend/scripts/export_plan_runs.py --plan-id "$PLAN" --format bundle --output-dir ./evidence
```

Both default to `$ORCHESTRATOR_HOME`; pass `--db` or `--orchestrator-home` to
read another install. They open SQLite read-only (`mode=ro`, `query_only=ON`),
construct no application container, and write nothing back. Secret, config and
capability tables are excluded, and provider/model/agent catalog fields are
redacted.

**Before you send it**, check what you are sending. The exports carry commands,
commit SHAs, file paths, briefs and chat history — that is the point — but a
brief or a log can contain something private. The orchestrator never writes
secret material to logs or exports (keys live encrypted and are referenced by
URI), so the risk is your content, not its credentials.

A useful report includes:

- the exact brief and the fixture version, if you used one
- the orchestrator version or commit SHA
- reasoner provider/model **and** agent runtime/provider/model
- plan, cycle, goal, task, run and attempt ids
- the timeline: retries, capacity waits, and any intervention you made
- the evidence document above, and the git refs
- what you expected versus what happened

Change **one variable per run series**, or two runs cannot be compared.

## What deletion destroys

`DELETE /api/plans/{id}` cascades to cycles, attempts, evidence, chat and
telemetry — every plan-scoped table declares `ON DELETE CASCADE`, and a test
fails if a new one does not. Export first. It is refused with 409 `PLAN_BUSY`
while a worker holds a live lease.
