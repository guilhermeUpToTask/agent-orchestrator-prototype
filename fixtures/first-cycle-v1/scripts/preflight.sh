#!/usr/bin/env bash
# Everything that must be true before a cycle can start, checked in the order an
# operator hits it. Each failure prints the one command that fixes it.
#
# The list is not decorative: every check here corresponds to a way a first run
# has actually been lost — an unmigrated database, a worker that was never
# started, an agent bound to a provider whose key cannot be decrypted.
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
api() { "$SCRIPT_DIR/api.sh" "$@"; }

ok=0
pass() { printf '  ✓ %s\n' "$*"; }
fail() { printf '  ✗ %s\n' "$*" >&2; ok=1; }
fix()  { printf '      fix: %s\n' "$*" >&2; }

command -v jq >/dev/null 2>&1 || { printf 'jq is required\n' >&2; exit 2; }

# 1. API reachable
if health="$(api GET /health 2>/dev/null)"; then
  pass "API is up (version $(jq -r .version <<<"$health"))"
else
  fail "API is not reachable at ${FIRST_CYCLE_API:-http://127.0.0.1:8000}"
  fix "backend/scripts/dev.sh start"
  exit 1
fi

# 2. Installation readiness — one call, the same one the Settings screen renders
readiness="$(api GET /api/readiness)" || { fail "GET /api/readiness failed"; exit 1; }
while read -r line; do
  name="${line%%|*}"; rest="${line#*|}"; status="${rest%%|*}"; detail="${rest#*|}"
  case "$status" in
    ok)   pass "$name" ;;
    warn) printf '  ! %s — %s\n' "$name" "$detail" ;;
    *)    fail "$name — $detail" ;;
  esac
done < <(jq -r '.checks[] | "\(.name)|\(.status)|\(.detail // "")"' <<<"$readiness")

# 3. A worker must actually be running. Leases prove a worker is BUSY; an idle
#    one holds none, so before the first claim "running, nothing to do" and
#    "never started" are indistinguishable without this read.
workers="$(api GET /api/workers)" || { fail "GET /api/workers failed"; exit 1; }
live="$(jq '[.[] | select(.stale == false)] | length' <<<"$workers")"
if [[ "$live" -gt 0 ]]; then
  pass "worker is alive ($(jq -r '[.[] | select(.stale==false) | .mode] | join(", ")' <<<"$workers"))"
else
  total="$(jq 'length' <<<"$workers")"
  if [[ "$total" -eq 0 ]]; then
    fail "no worker has ever reported in — nothing will pick the plan up"
  else
    fail "every worker is stale (last seen > 15s ago)"
  fi
  fix "backend/scripts/dev.sh start   (or: praxis worker start)"
fi

# 4. Runtime bindings — a broken one fails the FIRST attempt, not the setup
runner="$(api GET /api/runner/status)" || { fail "GET /api/runner/status failed"; exit 1; }
mode="$(jq -r .mode <<<"$runner")"
if [[ "$(jq -r '.valid' <<<"$runner")" == "true" ]]; then
  pass "agent runner: $mode"
else
  fail "agent runner ($mode): $(jq -r '.detail // "invalid"' <<<"$runner")"
  fix "praxis config set agent_runner.mode dry-run   # or repair the binding"
fi
while read -r line; do
  [[ -z "$line" ]] && continue
  fail "agent binding: $line"
  fix "check the agent's provider/model rows and PRAXIS_MASTER_KEY"
done < <(jq -r '.agents[]? | select(.valid == false) | "\(.agent_name) (\(.runtime_type)) — \(.detail // "invalid")"' <<<"$runner")

if [[ "$mode" == "real" ]]; then
  # Only the runtimes a BOUND agent actually uses. `/api/runner/status` probes
  # every supported CLI, so an install with three agents on `pi` still reports
  # `gemini` missing — true, and no reason to refuse to start. A missing binary
  # blocks the run only when something is bound to it.
  while read -r line; do
    [[ -z "$line" ]] && continue
    fail "binary: $line"
    fix "install the CLI, or bind the agent to a runtime you have"
  done < <(jq -r '
    ((.agents // []) | map(.runtime_type) | unique) as $bound
    | (.binaries // [])[]
    | select(.ok == false)
    | select(.name as $n | $bound | index($n))
    | "\(.name) — \(.message // "not found") (an agent is bound to it)"' <<<"$runner")

  # Unused runtimes are still worth saying out loud, just not fatally.
  while read -r line; do
    [[ -z "$line" ]] && continue
    printf '  ! %s\n' "$line"
  done < <(jq -r '
    ((.agents // []) | map(.runtime_type) | unique) as $bound
    | (.binaries // [])[]
    | select(.ok == false)
    | select(.name as $n | ($bound | index($n)) | not)
    | "\(.name) not installed — no agent is bound to it, so it cannot block this run"' <<<"$runner")
fi

# 5. Reasoner — Tier 1 needs a real one; Tier 0 must NOT have one
reasoner="$(api GET /api/reasoner/status)" || { fail "GET /api/reasoner/status failed"; exit 1; }
rmode="$(jq -r .mode <<<"$reasoner")"
if [[ "$(jq -r '.valid' <<<"$reasoner")" == "true" ]]; then
  pass "reasoner: $rmode $(jq -r '.model_name // .model_id // ""' <<<"$reasoner")"
else
  fail "reasoner ($rmode): $(jq -r '.detail // "invalid"' <<<"$reasoner")"
fi

# Tier discipline: stub+dry-run or llm+real, never one of each. A mixed pair
# produces a run whose evidence means nothing — a real plan verified by a dummy,
# or a dry-run plan costing real tokens.
if [[ "$rmode" == "llm" && "$mode" != "real" ]] || [[ "$rmode" == "stub" && "$mode" == "real" ]]; then
  fail "mixed tiers: reasoner=$rmode agent_runner=$mode"
  fix "Tier 0: reasoner.mode=stub + agent_runner.mode=dry-run"
  fix "Tier 1: reasoner.mode=llm  + agent_runner.mode=real"
fi

exit "$ok"
