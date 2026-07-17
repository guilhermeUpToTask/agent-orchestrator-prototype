#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
printf 'note: reseed_openrouter_key.sh is deprecated; prefer dev.sh seed\n' >&2
exec "$SCRIPT_DIR/dev.sh" seed --provider openrouter --api-key-env OPENROUTER_API_KEY "$@"
