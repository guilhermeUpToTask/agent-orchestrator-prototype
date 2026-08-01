#!/usr/bin/env bash
# Drive ONE complete cycle end to end, unattended.
#
# Every other fixture teaches the walkthrough a step at a time, which is right
# for hunting bugs and wrong for a first run: an operator who has never seen a
# gate cannot tell "waiting for me" from "stuck". This script performs the whole
# sequence — project, plan, discovery, intent gate, cycle draft gate, execution,
# publication — printing what it is waiting for at every step, and exits non-zero
# with the served reason the moment the plan stops being able to progress.
#
#   ./fixtures/first-cycle-v1/scripts/run-cycle.sh
#   ./fixtures/first-cycle-v1/scripts/run-cycle.sh --name my-run --timeout 2400
#
# Requires: API + worker running (backend/scripts/dev.sh start), and the target
# repo materialized (scripts/materialize.sh).
#
# Tier 1 (real reasoner + real agent runtime) is the intended mode: the point is
# to prove a REAL cycle, and the free models configured in the catalog make that
# affordable. Tier 0 (stub + dry-run) also completes, faster and for nothing.
# Never mix the two halves.
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
FIXTURE_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
api() { "$SCRIPT_DIR/api.sh" "$@"; }

NAME="first-cycle-v1-$RANDOM"
TIMEOUT=2400          # 40 min: a free model under load is slow, not broken
POLL=5
DISPOSITION="retain_branch"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name) NAME="$2"; shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --disposition) DISPOSITION="$2"; shift 2 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) printf 'unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

ORCH_HOME="${ORCHESTRATOR_HOME:-$HOME/.orchestrator}"
REPO="${FIRST_CYCLE_REPO:-$ORCH_HOME/first-cycle-v1/repo}"

die() { printf '\n✗ %s\n' "$*" >&2; exit 1; }
step() { printf '\n=== %s\n' "$*"; }
note() { printf '    %s\n' "$*"; }

command -v jq >/dev/null 2>&1 || die "jq is required"
[[ -d "$REPO/.git" ]] || die "target repo missing: $REPO (run scripts/materialize.sh)"

plan_json() { api GET "/api/plans/$PLAN_ID"; }
plan_field() { plan_json | jq -r "$1"; }

# The one place that decides a run is over. Anything the plan cannot leave on
# its own is a failure of the RUN, printed with the server's own explanation
# rather than a guess.
fail_on_terminal_state() {
  local doc="$1"
  local human_block
  human_block="$(jq -c '[(.block // empty), (.goal_blocks // {} | .[])]
    | map(select(.requires_human == true)) | .[0] // empty' <<<"$doc")"
  if [[ -n "$human_block" && "$human_block" != "null" ]]; then
    printf '\nblocked: %s\n' "$(jq -r '.explanation' <<<"$human_block")" >&2
    printf 'stage: %s   resolutions: %s\n' \
      "$(jq -r '.stage' <<<"$human_block")" \
      "$(jq -c '.legal_resolutions' <<<"$human_block")" >&2
    die "the plan opened a block that needs a human"
  fi
}

# ── 0. preflight ─────────────────────────────────────────────────────────────
step "0. preflight"
"$SCRIPT_DIR/preflight.sh" || die "preflight failed — fix the checks above first"

# ── 1. project ───────────────────────────────────────────────────────────────
step "1. create the project bound to $REPO"
PROJECT_ID="$(api POST /api/projects "$(jq -nc --arg n "$NAME" --arg r "$REPO" \
  '{name:$n, repo_url:$r}')" | jq -r .id)"
[[ -n "$PROJECT_ID" && "$PROJECT_ID" != "null" ]] || die "project was not created"
note "project=$PROJECT_ID"

# ── 2. plan + discovery ──────────────────────────────────────────────────────
step "2. open the plan with the locked brief"
BRIEF="$(jq -Rs . < "$FIXTURE_DIR/brief.txt")"
CREATED="$(api POST /api/plans "$(jq -nc --argjson b "$BRIEF" --arg p "$PROJECT_ID" \
  '{brief:$b, project_id:$p}')")"
PLAN_ID="$(jq -r .plan_id <<<"$CREATED")"
[[ -n "$PLAN_ID" && "$PLAN_ID" != "null" ]] || die "plan was not created"
note "plan=$PLAN_ID"
note "discovery: $(jq -r '.discovery_status // "unknown"' <<<"$CREATED")"

# Discovery is multi-turn by design: the first turn may end waiting for the
# operator. Reply until a turn commits, rather than assuming one round trip.
deadline=$((SECONDS + TIMEOUT))
while :; do
  doc="$(plan_json)"
  gate_type="$(jq -r '.pending_gate.subject_type // ""' <<<"$doc")"
  [[ "$gate_type" == "intent" ]] && break
  fail_on_terminal_state "$doc"
  (( SECONDS < deadline )) || die "no intent gate within ${TIMEOUT}s"

  activity="$(jq -r '.activity' <<<"$doc")"
  operation="$(jq -r '.planning_operation.status // "none"' <<<"$doc")"

  # Reply only when the reasoner is actually waiting on the operator. Keying on
  # `activity` alone would post a second turn into a session that is still
  # running — the plan reads `intent_discovery` for the whole of a turn, not
  # just at the end of one.
  if [[ "$activity" == "intent_discovery" && ( "$operation" == "waiting_for_user" || "$operation" == "none" ) ]]; then
    note "the reasoner is waiting on an answer — replying"
    api POST "/api/plans/$PLAN_ID/discovery/message" \
      '{"message":"Yes, that is exactly right. Keep it to one goal and the smallest task set, then proceed."}' \
      | jq -r '"    reasoner: " + (.reply | .[0:160])'
  else
    note "$(date +%H:%M:%S) $(jq -r .status <<<"$doc")/$activity — $(jq -r '.planning_progress // .planning_operation.status // "…"' <<<"$doc")"
    sleep "$POLL"
  fi
done

# ── 3. intent gate ───────────────────────────────────────────────────────────
step "3. approve the intent gate"
gate_body() { plan_json | jq -c '{gate_id: .pending_gate.id, subject_revision: .pending_gate.subject_revision}'; }
plan_json | jq -r '"    objective: " + (.intent_proposal.objective // "(none)" | .[0:200])'
api POST "/api/plans/$PLAN_ID/intent/approve" "$(gate_body)" >/dev/null
note "approved"

# ── 4. cycle draft gate ──────────────────────────────────────────────────────
step "4. wait for the cycle draft, then approve it"
while :; do
  doc="$(plan_json)"
  [[ "$(jq -r '.pending_gate.subject_type // ""' <<<"$doc")" == "cycle_draft" ]] && break
  fail_on_terminal_state "$doc"
  (( SECONDS < deadline )) || die "no cycle draft within the time budget"
  note "planning… (activity=$(jq -r .activity <<<"$doc"))"
  sleep "$POLL"
done
plan_json | jq -r '.cycle_draft.goals
  | "    draft: \(length) goal(s)", (.[] | "      - \(.key): \(.name)")'
api POST "/api/plans/$PLAN_ID/cycle-draft/approve" "$(gate_body)" >/dev/null
note "cycle activated"

# ── 5. execution ─────────────────────────────────────────────────────────────
step "5. execution — enrichment, agent attempts, verification, promotion"
last=""
while :; do
  doc="$(plan_json)"
  [[ "$(jq -r '.pending_gate.subject_type // ""' <<<"$doc")" == "cycle_completion" ]] && break
  fail_on_terminal_state "$doc"
  (( SECONDS < deadline )) || die "the cycle did not reach publication within ${TIMEOUT}s"

  # One line per CHANGE, so a long free-model run is readable afterwards.
  now="$(jq -r '"\(.status)/\(.activity)"' <<<"$doc")"
  waiting="$(jq -r '.provider_waiting // empty | "  [provider waiting: \(.safe_message)]"' <<<"$doc")"
  if [[ "$now$waiting" != "$last" ]]; then
    printf '    %s %s%s\n' "$(date +%H:%M:%S)" "$now" "$waiting"
    last="$now$waiting"
  fi
  sleep "$POLL"
done

# ── 6. publication ───────────────────────────────────────────────────────────
step "6. record the output disposition ($DISPOSITION)"
CYCLE_ID="$(plan_field '.active_cycle.id')"
BODY="$(plan_json | jq -c --arg d "$DISPOSITION" --arg c "cycle/$(plan_field '.active_cycle.id')" \
  '{gate_id: .pending_gate.id, subject_revision: .pending_gate.subject_revision,
    disposition: $d, output_reference: $c}')"
api POST "/api/plans/$PLAN_ID/publication" "$BODY" >/dev/null
note "cycle=$CYCLE_ID disposition=$DISPOSITION"

# ── 7. evidence ──────────────────────────────────────────────────────────────
step "7. verified evidence (the reason to trust the run)"
api GET "/api/plans/$PLAN_ID/cycles/$CYCLE_ID/evidence" | jq '{
  cycle_status,
  disposition,
  accepted: [.goals[].tasks[].accepted_evidence[] | {exact_command, exit_code, candidate_commit_sha}],
  promotions: [.goals[] | select(.promotion != null) | .promotion | {from_ref, into_ref, merge_sha}],
  unattributed_evidence_refs
}'

printf '\n✓ cycle complete\n'
printf '  plan=%s  project=%s  cycle=%s\n' "$PLAN_ID" "$PROJECT_ID" "$CYCLE_ID"
printf '  verify:  python3 %s/verify_run.py --plan %s --repo %s\n' \
  "$SCRIPT_DIR" "$PLAN_ID" "$REPO"
printf '  guards:  %s/guards.sh\n' "$SCRIPT_DIR"
