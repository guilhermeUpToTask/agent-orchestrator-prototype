#!/usr/bin/env bash
# Asserts exactly the six capabilities the devcontainer could not provide.
# This script is the artifact P8.5's environment work is judged by.
set -uo pipefail

PASS=0
FAIL=0

check() {
  local name="$1"; shift
  if "$@" >/tmp/verify-out 2>&1; then
    echo "PASS  $name"
    PASS=$((PASS + 1))
  else
    echo "FAIL  $name"
    sed 's/^/        /' /tmp/verify-out | tail -5
    FAIL=$((FAIL + 1))
  fi
}

echo "=== aipom-dev capability proof ==="

# 1. Bubblewrap with a fresh procfs — the exact call that failed in the devcontainer.
check "bwrap mounts a fresh /proc" \
  bwrap --ro-bind / / --unshare-user --unshare-pid --proc /proc /bin/true

# 2. A fresh procfs inside a private PID namespace.
check "fresh procfs in a private PID namespace" \
  unshare --user --map-root-user --pid --fork --mount-proc /bin/true

# 3. cgroup2 is writable, and mounts inside a user namespace.
check "cgroup2 is writable" \
  bash -c 'mkdir -p /sys/fs/cgroup/aipom-probe && rmdir /sys/fs/cgroup/aipom-probe'
check "cgroup2 mounts in a user namespace" \
  unshare -Urm --propagation private \
    bash -c 'mkdir -p /tmp/cg && mount -t cgroup2 none /tmp/cg'

# 4. podman WITHOUT --cgroups=disabled, with a private PID namespace.
#    Assert the capability itself (the container's own process is PID 1),
#    not just that the flag was accepted. --network=none skips tap-device
#    setup so the check fails on the capability under test, not on networking.
check "podman runs with cgroups and a private PID namespace" \
  podman run --rm --pid=private --network=none docker.io/library/alpine:3.20 sh -c '[ "$$" -eq 1 ]'

# 5. docker with a private PID namespace (the default — no runtime-specific flag).
check "docker runs with a private PID namespace" \
  docker run --rm --network=none docker.io/library/alpine:3.20 sh -c '[ "$$" -eq 1 ]'

# 6. Rootless podman with full isolation.
check "rootless podman runs with full isolation" \
  podman --runtime crun run --rm --userns=auto --network=none docker.io/library/alpine:3.20 /bin/true

echo "=== $PASS passed, $FAIL failed ==="
[[ "$FAIL" -eq 0 ]]
