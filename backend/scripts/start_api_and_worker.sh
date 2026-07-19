#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
printf 'note: start_api_and_worker.sh is deprecated; prefer dev.sh start\n' >&2
exec "$SCRIPT_DIR/dev.sh" start "$@"
