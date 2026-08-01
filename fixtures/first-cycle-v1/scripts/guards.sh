#!/usr/bin/env bash
# The critical defects found in the Phase 4/5 review, checked over the live API.
#
# Each has a unit or integration test already; this exists because those run
# against a TestClient and a tmp database, and the failures they cover are ones
# an operator meets through a running server against a real state directory.
# A green suite plus a red guard here means the deployment, not the code.
#
#   ./fixtures/first-cycle-v1/scripts/guards.sh            # no plan needed
#   ./fixtures/first-cycle-v1/scripts/guards.sh --plan ID  # adds the log-tail guard
#
# Safe to run against a live install: it creates one throwaway provider and
# deletes it, and otherwise only reads.
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
api() { "$SCRIPT_DIR/api.sh" "$@"; }

PLAN_ID=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --plan) PLAN_ID="$2"; shift 2 ;;
    *) printf 'unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

failures=0
pass() { printf '  ✓ %s\n' "$*"; }
fail() { printf '  ✗ %s\n' "$*" >&2; failures=$((failures + 1)); }

# Expect a specific non-2xx. `api.sh` exits 1 on non-2xx and prints the body, so
# a guard asserts on the body's error code rather than the exit status alone.
refuses() {
  local label="$1" method="$2" path="$3" body="$4" expect="$5"
  local out
  if out="$(api "$method" "$path" "$body" 2>/dev/null)"; then
    fail "$label — accepted (expected refusal)"
    return
  fi
  if grep -qi -- "$expect" <<<"$out"; then
    pass "$label"
  else
    fail "$label — refused, but not for the expected reason: $(head -c 200 <<<"$out")"
  fi
}

printf '\n=== capacity bounds (a value below 1 declines every attempt forever)\n'
for bad in 0 -1; do
  refuses "provider max_inflight=$bad refused" POST /api/providers \
    "$(jq -nc --argjson m "$bad" '{name:"guard", base_url:"http://x", api_key:"k", max_inflight:$m}')" \
    "greater than or equal to 1"
done

printf '\n=== capacity scope (an unknown scope degrades to per_model at every read)\n'
refuses "capacity_scope typo refused" POST /api/providers \
  '{"name":"guard","base_url":"http://x","api_key":"k","capacity_scope":"endpoint-wide"}' \
  "capacity_scope"

printf '\n=== the bound must not cost the feature it guards\n'
if created="$(api POST /api/providers \
  '{"name":"first-cycle-guard","base_url":"http://x","api_key":"k","max_inflight":4,"capacity_scope":"endpoint_wide"}' 2>/dev/null)"; then
  id="$(jq -r .id <<<"$created")"
  if [[ "$(jq -r .max_inflight <<<"$created")" == "4" \
     && "$(jq -r .capacity_scope <<<"$created")" == "endpoint_wide" ]]; then
    pass "a valid capacity round-trips"
  else
    fail "a valid capacity did not round-trip: $(jq -c '{max_inflight, capacity_scope}' <<<"$created")"
  fi
  # model bound, on the same throwaway provider
  refuses "model max_inflight=0 refused" POST "/api/providers/$id/models" \
    '{"name":"guard-model","max_inflight":0}' "greater than or equal to 1"
  api DELETE "/api/providers/$id" >/dev/null 2>&1 || fail "could not clean up guard provider $id"
else
  fail "a valid provider was refused: $(head -c 200 <<<"$created")"
fi

printf '\n=== repository binding (the refusal must name the real cause)\n'
out="$(api POST /api/projects \
  '{"name":"guard","repo_url":"git@github.com:acme/widgets.git"}' 2>/dev/null || true)"
if grep -q "scp-style" <<<"$out" && ! grep -q "does not exist" <<<"$out"; then
  pass "an scp-style remote is refused by name"
else
  fail "scp-style refusal is wrong: $(head -c 240 <<<"$out")"
fi

printf '\n=== attempt-log resume (a per-batch offset skips lines on reconnect)\n'
if [[ -z "$PLAN_ID" ]]; then
  printf '  – skipped (pass --plan ID after a run to check this)\n'
else
  attempt="$(api GET "/api/plans/$PLAN_ID/attempts" \
    | jq -r '[.tasks[].runs[].attempts[]?] | .[0].id // empty')"
  if [[ -z "$attempt" ]]; then
    printf '  – skipped (plan %s has no attempts yet)\n' "$PLAN_ID"
  else
    entries="$(api GET "/api/plans/$PLAN_ID/attempts/$attempt/log" | jq '.entries | length')"
    if [[ "$entries" -gt 0 ]]; then
      pass "captured log for attempt $attempt has $entries entries"
    else
      printf '  – attempt %s captured no output (nothing to resume)\n' "$attempt"
    fi
  fi
fi

printf '\n'
if [[ "$failures" -eq 0 ]]; then
  printf '✓ all guards hold\n'
else
  printf '✗ %s guard(s) failed\n' "$failures" >&2
fi
exit "$(( failures > 0 ))"
