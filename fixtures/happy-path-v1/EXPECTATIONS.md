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

A run is **green** only if every item holds:

1. [ ] Intent review gate opened and was approved once  
2. [ ] Cycle draft approved once (1 goal preferred)  
3. [ ] Head goal enriched without a stuck `reasoner_failure` block  
4. [ ] Task(s) reached DONE with accepted verification evidence  
5. [ ] Goal promoted; cycle reached publication  
6. [ ] Publication disposition recorded (`retain_branch` is fine)  
7. [ ] `./scripts/check-success.sh` exits 0 on the promoted work (or plan/cycle branch checkout)  
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
# plan id from UI or plan list
python backend/scripts/snapshot_current_plan.py --plan-id <ID> --pretty \
  -o "$ORCHESTRATOR_HOME/happy-path-v1/runs/$(date -u +%Y%m%dT%H%M%SZ).json"
```

Optional: worker log tail, `GET /api/plans/{id}/attempts`, attempt log stream.
