#!/usr/bin/env bash
# Collect one happy-path-v1 run into a single consistently named directory.
#
#   ./scripts/capture-run.sh "$PLAN_ID"            # Tier 0
#   ./scripts/capture-run.sh "$PLAN_ID" 1          # Tier 1
#
# ROADMAP Phase 0 asks for the plan snapshot, evidence bundle, worker-log
# reference, fixture version and orchestrator Git SHA in ONE run directory. Two
# runs are only comparable if you can say what code and what fixture produced
# each — a snapshot with no version stamp is an anecdote.
#
# Writes to  $PRAXIS_HOME/happy-path-v1/runs/<UTC>-tier<N>-<plan prefix>/
#
#   manifest.json      versions, ids, tier, pinned runtime config, checks summary
#   plan.json          snapshot_current_plan.py — the debugging snapshot
#   verification.json  verify_run.py --json — the binary success contract
#   attempts.json      GET /api/plans/{id}/attempts — the attempt timeline
#   agent-events.json  GET /api/plans/{id}/agent-events — runtime telemetry
#   runner-status.json GET /api/runner/status — mode, bindings, binary probes
#   reasoner-status.json
#   bundle/            export_plan_runs.py --format bundle
#   worker-log.txt     copied when HAPPY_PATH_WORKER_LOG points at one
#
# Reads only. Nothing here mutates a plan, a branch, or the database. Exits 0
# when the run is green, 1 when a check failed (the capture still completes —
# a red run's evidence is the point), 2 when collection itself broke.
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
FIXTURE_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
REPO_ROOT="$(cd -- "$FIXTURE_DIR/../.." && pwd -P)"
FIXTURE_VERSION="$(basename -- "$FIXTURE_DIR")"

die() { printf 'error: %s\n' "$*" >&2; exit 2; }
note() { printf '==> %s\n' "$*"; }

PLAN_ID="${1:-}"
TIER="${2:-0}"
# A project owns one long-lived plan, so cycles accumulate across runs. Empty
# means "the most recently completed cycle" — right for the run just finished.
CYCLE_ID="${3:-}"
[[ -n "$PLAN_ID" ]] || die "usage: capture-run.sh <plan-id> [tier] [cycle-id]"
[[ "$TIER" == "0" || "$TIER" == "1" ]] || die "tier must be 0 or 1 (got: $TIER)"

command -v jq >/dev/null 2>&1 || die "jq is required"
command -v python3 >/dev/null 2>&1 || die "python3 is required"

# Mirrors praxis_orchestrator/infra/env_compat.py: an explicit home wins, a
# fresh install is ~/.praxis, and a pre-rename ~/.orchestrator is adopted in
# place rather than left behind holding the operator's only database.
ORCH_HOME="${PRAXIS_HOME:-${ORCHESTRATOR_HOME:-$HOME/.praxis}}"
[ -d "$ORCH_HOME" ] || [ ! -d "$HOME/.orchestrator" ] || ORCH_HOME="$HOME/.orchestrator"
HAPPY_PATH_REPO="${HAPPY_PATH_REPO:-$ORCH_HOME/happy-path-v1/repo}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="$ORCH_HOME/happy-path-v1/runs/${STAMP}-tier${TIER}-${PLAN_ID:0:8}"

[[ -e "$RUN_DIR" ]] && die "run directory already exists: $RUN_DIR"
mkdir -p "$RUN_DIR"
note "capturing into $RUN_DIR"

api() { "$SCRIPT_DIR/api.sh" "$@"; }

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
capture planning-artifacts.json GET "/api/plans/$PLAN_ID/planning-artifacts"
capture runner-status.json GET /api/runner/status
capture reasoner-status.json GET /api/reasoner/status

note "plan snapshot"
python3 "$REPO_ROOT/backend/scripts/snapshot_current_plan.py" \
  --plan-id "$PLAN_ID" --pretty --output "$RUN_DIR/plan.json" \
  || note "warning: snapshot_current_plan.py failed"

note "evidence bundle"
python3 "$REPO_ROOT/backend/scripts/export_plan_runs.py" \
  --plan-id "$PLAN_ID" --format bundle --output-dir "$RUN_DIR/bundle" \
  || note "warning: export_plan_runs.py failed"

note "verification (tier $TIER)"
VERIFY_EXIT=0
verify_args=(--plan-id "$PLAN_ID" --tier "$TIER" --json)
[[ -n "$CYCLE_ID" ]] && verify_args+=(--cycle-id "$CYCLE_ID")
python3 "$SCRIPT_DIR/verify_run.py" "${verify_args[@]}" \
  >"$RUN_DIR/verification.json" || VERIFY_EXIT=$?
# Exit 2 is a broken harness, not a failed run; keep the distinction in the file.
if [[ "$VERIFY_EXIT" -ge 2 ]]; then
  note "warning: verify_run.py could not collect facts (exit $VERIFY_EXIT)"
fi

if [[ -n "${HAPPY_PATH_WORKER_LOG:-}" && -f "$HAPPY_PATH_WORKER_LOG" ]]; then
  cp -- "$HAPPY_PATH_WORKER_LOG" "$RUN_DIR/worker-log.txt"
  note "worker log copied from $HAPPY_PATH_WORKER_LOG"
fi

# --- the version stamp, without which none of the above is comparable ---
ORCH_SHA="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
ORCH_DIRTY=false
git -C "$REPO_ROOT" diff --quiet 2>/dev/null || ORCH_DIRTY=true
FIXTURE_SHA="$(git -C "$REPO_ROOT" log -1 --format=%H -- "fixtures/$FIXTURE_VERSION" 2>/dev/null || echo unknown)"
SEED_SHA="$(git -C "$HAPPY_PATH_REPO" rev-parse "happy-path-v1-seed" 2>/dev/null || echo unknown)"

read_json() { # read_json <file> — the file's contents, or null
  if [[ -s "$RUN_DIR/$1" ]]; then cat "$RUN_DIR/$1"; else echo null; fi
}

jq -n \
  --arg captured_at "$STAMP" \
  --arg plan_id "$PLAN_ID" \
  --arg tier "$TIER" \
  --arg fixture "$FIXTURE_VERSION" \
  --arg fixture_sha "$FIXTURE_SHA" \
  --arg orchestrator_sha "$ORCH_SHA" \
  --argjson orchestrator_dirty "$ORCH_DIRTY" \
  --arg seed_sha "$SEED_SHA" \
  --arg repo "$HAPPY_PATH_REPO" \
  --arg worker_log "${HAPPY_PATH_WORKER_LOG:-}" \
  --argjson plan "$(read_json plan-detail.json)" \
  --argjson runner "$(read_json runner-status.json)" \
  --argjson reasoner "$(read_json reasoner-status.json)" \
  --argjson verification "$(read_json verification.json)" \
  '{
    fixture: {name: $fixture, last_commit: $fixture_sha, seed_commit: $seed_sha},
    orchestrator: {commit: $orchestrator_sha, working_tree_dirty: $orchestrator_dirty},
    run: {
      captured_at: $captured_at,
      tier: ($tier | tonumber),
      plan_id: $plan_id,
      project_id: ($plan.project_id? // null),
      cycle_id: (($plan.cycles? // []) | last | .id? // $plan.active_cycle?.id // null),
      status: ($plan.status? // null),
      repository: $repo,
      worker_log_reference: (if $worker_log == "" then null else $worker_log end)
    },
    pinned_runtime: {
      runner_mode: ($runner.mode? // null),
      # `//` treats false as empty, so a boolean must never use it: a red run
      # reporting `green: null` instead of `green: false` is unreadable evidence.
      runner_valid: (if $runner == null then null else $runner.valid end),
      agents: [($runner.agents? // [])[] | {agent_name, runtime_type, provider_id, model_id, valid}],
      reasoner_mode: ($reasoner.mode? // null),
      reasoner_provider_id: ($reasoner.provider_id? // null),
      reasoner_model_id: ($reasoner.model_id? // null)
    },
    verification: {
      green: (if $verification == null then null else $verification.green end),
      failed_checks: [($verification.checks? // [])[] | select(.ok == false) | .name]
    }
  }' >"$RUN_DIR/manifest.json"

note "manifest:"
jq -C '{fixture, orchestrator, run: (.run | {tier, plan_id, cycle_id, status}), verification}' \
  "$RUN_DIR/manifest.json"

if [[ "$VERIFY_EXIT" -eq 0 ]]; then
  note "RUN GREEN — evidence in $RUN_DIR"
  exit 0
fi
note "RUN NOT GREEN — evidence in $RUN_DIR (see verification.json)"
exit "$VERIFY_EXIT"
