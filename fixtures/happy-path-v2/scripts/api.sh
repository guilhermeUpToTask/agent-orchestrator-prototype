#!/usr/bin/env bash
# Thin curl wrapper for driving the happy-path fixture over the backend API only.
#
#   ./scripts/api.sh GET  /api/plans
#   ./scripts/api.sh POST /api/projects '{"name":"happy-path-v2"}'
#   ./scripts/api.sh POST "/api/plans/$PLAN/intent/approve" "$BODY"
#
# Base URL:  HAPPY_PATH_API (default http://127.0.0.1:8000)
# Auth:      ORCHESTRATOR_API_TOKEN (sent as Authorization: Bearer …; omitted when unset)
#
# Prints the response body on 2xx and exits 0; prints status + body and exits 1
# otherwise, so the operator loop can branch on it.
set -Eeuo pipefail
IFS=$'\n\t'

die() { printf 'error: %s\n' "$*" >&2; exit 2; }

command -v curl >/dev/null 2>&1 || die "curl is required"

METHOD="${1:-}"
PATH_PART="${2:-}"
BODY="${3:-}"
[[ -n "$METHOD" && -n "$PATH_PART" ]] || die "usage: api.sh <METHOD> </api/path> [json-body]"
[[ "$PATH_PART" == /* ]] || die "path must start with / (got: $PATH_PART)"

BASE="${HAPPY_PATH_API:-http://127.0.0.1:8000}"

args=(--silent --show-error --request "$METHOD" --write-out '\n%{http_code}')
if [[ -n "${ORCHESTRATOR_API_TOKEN:-}" ]]; then
  args+=(--header "Authorization: Bearer $ORCHESTRATOR_API_TOKEN")
fi
if [[ -n "$BODY" ]]; then
  args+=(--header 'Content-Type: application/json' --data "$BODY")
fi

response="$(curl "${args[@]}" "$BASE$PATH_PART")" || die "request failed: $METHOD $PATH_PART"
status="${response##*$'\n'}"
payload="${response%$'\n'*}"

printf '%s\n' "$payload"
if [[ "$status" =~ ^2 ]]; then
  exit 0
fi
printf 'HTTP %s for %s %s\n' "$status" "$METHOD" "$PATH_PART" >&2
exit 1
