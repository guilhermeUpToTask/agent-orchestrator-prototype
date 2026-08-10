#!/usr/bin/env bash
# Collect one static-site-v1 demo run into a published evidence directory.
#
#   ./demos/static-site-v1/scripts/capture-run.sh "$PLAN_ID" [cycle-id]
#
# A demo is not a fixture: it runs once, it is captured, and the capture is
# COMMITTED — including a red one, which is why this exits 0 whenever
# collection succeeded and reports the verdict in the manifest instead of in
# its exit code. `fixtures/happy-path-v1/scripts/capture-run.sh` does the
# opposite on purpose (a red fixture run is a bug and its exit code should say
# so). That difference is the whole reason this is a separate script rather
# than a flag on that one.
#
# Writes to  demos/static-site-v1/runs/<UTC>-<plan prefix>/
#
#   manifest.json       versions, ids, pinned models, verdicts, wall-clock, cost
#   plan-detail.json    GET /api/plans/{id} — the aggregate read model
#   attempts.json       the attempt timeline (what ran, on which model, when)
#   agent-events.json   runtime telemetry
#   acceptance.json     the cycle acceptance runs — the container verdicts
#   runner-status.json  mode, bindings, binary probes
#   reasoner-status.json
#   structural.txt      verify_demo.py output — the orchestration checks
#   worker-log.txt      copied when DEMO_WORKER_LOG points at one
#
# Reads only. Nothing here mutates a plan, a branch, or the database.
# Exit 0 collection succeeded (read the manifest for the verdict) · 2 the
# harness itself broke and there is nothing to publish.
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
DEMO_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
REPO_ROOT="$(cd -- "$DEMO_DIR/../.." && pwd -P)"

die() { printf 'error: %s\n' "$*" >&2; exit 2; }
note() { printf '==> %s\n' "$*"; }

PLAN_ID="${1:-}"
# A project owns one long-lived plan, so cycles accumulate across runs. Empty
# means "the newest cycle" — right for the run just finished.
CYCLE_ID="${2:-}"
[[ -n "$PLAN_ID" ]] || die "usage: capture-run.sh <plan-id> [cycle-id]"

command -v jq >/dev/null 2>&1 || die "jq is required"
command -v python3 >/dev/null 2>&1 || die "python3 is required"

ORCH_HOME="${ORCHESTRATOR_HOME:-$HOME/.orchestrator}"
SITE_REPO="${STATIC_SITE_REPO:-$ORCH_HOME/demos/static-site-v1/repo}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="$DEMO_DIR/runs/${STAMP}-${PLAN_ID:0:8}"

[[ -e "$RUN_DIR" ]] && die "run directory already exists: $RUN_DIR"
mkdir -p "$RUN_DIR"
note "capturing into $RUN_DIR"

API="${DEMO_API:-$REPO_ROOT/fixtures/first-cycle-v1/scripts/api.sh}"
api() { "$API" "$@"; }

# Best-effort reads: a missing optional artifact must not lose the ones we have.
capture() { # capture <file> <method> <path>
  local out="$1"; shift
  if api "$@" >"$RUN_DIR/$out" 2>"$RUN_DIR/$out.err"; then
    rm -f "$RUN_DIR/$out.err"
  else
    note "warning: could not capture $out (see $out.err)"
  fi
}

capture plan-detail.json GET "/api/plans/$PLAN_ID"
capture attempts.json GET "/api/plans/$PLAN_ID/attempts"
capture agent-events.json GET "/api/plans/$PLAN_ID/agent-events"
capture runner-status.json GET /api/runner/status
capture reasoner-status.json GET /api/reasoner/status

read_json() { # read_json <file> — the file's contents, or null
  if [[ -s "$RUN_DIR/$1" ]]; then cat "$RUN_DIR/$1"; else echo null; fi
}

# The cycle to report on. An explicit id wins; otherwise the newest one.
if [[ -z "$CYCLE_ID" ]]; then
  CYCLE_ID="$(read_json plan-detail.json |
    jq -r '(.active_cycle.id // (.cycles // [] | last | .id) // "")')"
fi
[[ -n "$CYCLE_ID" && "$CYCLE_ID" != "null" ]] || note "warning: no cycle id resolved"

if [[ -n "$CYCLE_ID" && "$CYCLE_ID" != "null" ]]; then
  # The acceptance verdicts ride on the cycle evidence document; there is no
  # separate acceptance endpoint.
  capture evidence.json GET "/api/plans/$PLAN_ID/cycles/$CYCLE_ID/evidence"
fi

note "structural checks"
STRUCTURAL_EXIT=0
python3 "$SCRIPT_DIR/verify_demo.py" \
  --plan-id "$PLAN_ID" --cycle-id "$CYCLE_ID" --repo "$SITE_REPO" \
  --seed-tag static-site-v1-seed >"$RUN_DIR/structural.txt" 2>&1 || STRUCTURAL_EXIT=$?
# 0 passed · 1 a real finding · 2 the harness is broken. Never conflate the
# last two: "nothing was checked" must not be published as "nothing was wrong".
note "verify_demo.py exit $STRUCTURAL_EXIT"

if [[ -n "${DEMO_WORKER_LOG:-}" && -f "$DEMO_WORKER_LOG" ]]; then
  cp -- "$DEMO_WORKER_LOG" "$RUN_DIR/worker-log.txt"
  note "worker log copied from $DEMO_WORKER_LOG"
fi

# --- the version stamp, without which none of the above is comparable ---
ORCH_SHA="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
ORCH_DIRTY=false
git -C "$REPO_ROOT" diff --quiet 2>/dev/null || ORCH_DIRTY=true
SEED_SHA="$(git -C "$SITE_REPO" rev-parse "static-site-v1-seed" 2>/dev/null || echo unknown)"

jq -n \
  --arg captured_at "$STAMP" \
  --arg plan_id "$PLAN_ID" \
  --arg cycle_id "$CYCLE_ID" \
  --arg orchestrator_sha "$ORCH_SHA" \
  --argjson orchestrator_dirty "$ORCH_DIRTY" \
  --arg seed_sha "$SEED_SHA" \
  --arg repo "$SITE_REPO" \
  --arg structural_exit "$STRUCTURAL_EXIT" \
  --arg wall_clock "${DEMO_WALL_CLOCK:-}" \
  --arg cost "${DEMO_COST_USD:-}" \
  --argjson plan "$(read_json plan-detail.json)" \
  --argjson runner "$(read_json runner-status.json)" \
  --argjson reasoner "$(read_json reasoner-status.json)" \
  --argjson evidence "$(read_json evidence.json)" \
  '{
    demo: {name: "static-site-v1", seed_commit: $seed_sha},
    orchestrator: {commit: $orchestrator_sha, working_tree_dirty: $orchestrator_dirty},
    run: {
      captured_at: $captured_at,
      plan_id: $plan_id,
      cycle_id: (if $cycle_id == "" then null else $cycle_id end),
      project_id: ($plan.project_id? // null),
      status: ($plan.status? // null),
      repository: $repo,
      wall_clock: (if $wall_clock == "" then null else $wall_clock end),
      cost_usd: (if $cost == "" then null else $cost end)
    },
    pinned_runtime: {
      runner_mode: ($runner.mode? // null),
      # `//` treats false as empty, so a boolean must never use it: a red run
      # reporting `valid: null` instead of `valid: false` is unreadable evidence.
      runner_valid: (if $runner == null then null else $runner.valid end),
      agents: [($runner.agents? // [])[] | {agent_name, runtime_type, provider_id, model_id, valid}],
      reasoner_mode: ($reasoner.mode? // null),
      reasoner_model: ($reasoner.model? // $reasoner.model_id? // null)
    },
    verdicts: {
      structural_exit: ($structural_exit | tonumber),
      acceptance: [($evidence.acceptance_runs? // [])[]?
                   | {trigger, outcome, summary}]
    }
  }' >"$RUN_DIR/manifest.json"

note "manifest:"
jq -C '{demo, orchestrator, run, pinned_runtime: (.pinned_runtime | {runner_mode, reasoner_model}), verdicts}' \
  "$RUN_DIR/manifest.json"

note "captured — publish this directory as-is, green or red"
