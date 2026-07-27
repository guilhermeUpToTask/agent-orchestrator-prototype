# Happy-path v1 — expectations

## Size budget (hard contract)

| Metric | Target | Treat as finding if |
|---|---|---|
| Goals after draft approve | **1** | ≥ 3 without operator edit |
| Tasks after head enrichment | **1–2** | ≥ 4 |
| Agent attempts (clean real run) | **1–3** | ≥ 8 with no code progress |
| Wall clock (real mode) | **10–25 min** | > 45 min |
| Unexpected human interventions | **0** | any block/pause you did not inject |

If the cycle draft invents CI, docs, typing rollout, edge-case matrices, or a
mini-framework as separate goals — **edit or replan** back to one implement goal.
That is a valid architect/enrichment finding, not something to “push through.”

## Binary success (all required)

These are checked for you — `scripts/verify_run.py --plan-id "$PLAN_ID"` asserts
1-6 and 8 from persisted plan and Git facts, and `--tier 1` adds 7. The list
below stays the human-readable contract; the script is what a run is measured
against.

A run is **green** only if every item holds:

1. [ ] Intent review gate opened and was approved once  
2. [ ] Cycle draft approved once (1 goal preferred)  
3. [ ] Head goal enriched without a stuck `reasoner_failure` block  
4. [ ] Task(s) reached DONE with accepted verification evidence  
5. [ ] Goal promoted; cycle reached publication  
6. [ ] Publication disposition recorded (`retain_branch` is fine)  
7. [ ] `./scripts/check-success.sh` exits 0 on a checkout of the cycle branch — the implementation is correct, a check was authored, AND that check fails against a deliberately broken `greet` (Tier 1 only; in Tier 0 the dry-run runner writes no implementation, so exit 1 there is expected)  
8. [ ] Seed default branch still matches `happy-path-v1-seed` content for `greeter.py` until promotion (orchestrator must not rewrite main)  

## Failure taxonomy (how to file)

| Symptom | Likely layer | Notes |
|---|---|---|
| Draft has many goals / empty deps | reasoner / architect | Capture draft JSON |
| Enrichment fans into many tiny tasks | enrichment contract | ROADMAP item 35 class |
| Rate limit → human block too early | capacity / retry | capacity-as-waiting design |
| `legal_actions` includes pause but POST fails | domain/API mismatch | cyclic pause guards |
| Worker hot-loop / tick_failed storm | execution handler | poison plan path |
| pytest never runs / wrong cwd | verification / workspace | attempt log |
| Code correct but no evidence / no promote | evidence / promotion | unpromotable goal |

## Capture on every run (green or red)

```bash
# plan id from the API (list order is not recency — sort explicitly):
#   ./scripts/api.sh GET /api/plans | jq -r 'sort_by(.updated_at) | last | .id'
./scripts/capture-run.sh "$PLAN_ID" [0|1]
```

One directory per run — `$ORCHESTRATOR_HOME/happy-path-v1/runs/<UTC>-tier<N>-<plan
prefix>/` — holding `manifest.json` (fixture version, seed commit, orchestrator
SHA + dirty flag, pinned reasoner/runner/agent bindings, failed checks),
`verification.json`, `plan.json`, `attempts.json`, `agent-events.json`,
`planning-artifacts.json`, the `bundle/`, and the worker log when
`HAPPY_PATH_WORKER_LOG` names one.

A snapshot with no version stamp is an anecdote: without the orchestrator SHA and
the pinned model, two runs cannot be compared and a regression cannot be dated.
Everything here is collected over HTTP and git — the walkthrough never needs the UI.
