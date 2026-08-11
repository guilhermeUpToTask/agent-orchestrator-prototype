#!/usr/bin/env bash
# Asserts the capabilities the devcontainer could not provide, plus (since
# 2026-08-10) the one Phase 9 added: a browser that launches.
# This script is the artifact P8.5's environment work is judged by.
#
# Before concluding the guest is incapable: Ubuntu 24.04 ships
# kernel.apparmor_restrict_unprivileged_userns=1. Checks 1, 2, 4 and 7 each
# create an unprivileged user namespace; if the stock AppArmor profiles don't
# permit that here, those four checks false-fail exactly like a kernel wall
# would. Run `sysctl kernel.apparmor_restrict_unprivileged_userns` first.
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

echo "=== praxis-dev capability proof ==="

# 1. Bubblewrap with a fresh procfs — the exact call that failed in the devcontainer.
check "bwrap mounts a fresh /proc" \
  bwrap --ro-bind / / --unshare-user --unshare-pid --proc /proc /bin/true

# 2. A fresh procfs inside a private PID namespace.
check "fresh procfs in a private PID namespace" \
  unshare --user --map-root-user --pid --fork --mount-proc /bin/true

# 3. cgroup2 is writable, and mounts inside a user namespace.
#    The cgroup2 root is 755 root:root on every systemd host, so this must
#    run as root (via the dev user's NOPASSWD sudo) to test the capability
#    the devcontainer's read-only mount actually denies — not permissions.
check "cgroup2 is writable" \
  bash -c 'sudo mkdir -p /sys/fs/cgroup/praxis-probe && sudo rmdir /sys/fs/cgroup/praxis-probe'
# -C (unshare the cgroup namespace) is load-bearing, not decoration. Mounting
# cgroup2 from a non-initial user namespace needs CAP_SYS_ADMIN over the cgroup
# namespace's OWNING user namespace. Without -C the process stays in the initial
# cgroup namespace, owned by the initial userns, so the new userns has no
# authority over it and the mount returns EPERM no matter how capable the host
# is. Dropping -C makes this check unpassable by construction.
check "cgroup2 mounts in a user namespace" \
  unshare -UrmC --propagation private \
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

# 7. A headless browser actually LAUNCHES. Added 2026-08-10 for Phase 9, whose
#    first act is a browser test suite. Playwright ships the browser binary but
#    not the system libraries it links against, so a guest missing them fails at
#    launch with `libatk-1.0.so.0: cannot open shared object file` — every spec
#    red, in a way that reads like a broken suite rather than a missing package.
#    Observed exactly that on a built guest before the cloud-init list gained
#    them. Skips rather than fails when the browser is not downloaded yet: the
#    binary is a per-checkout `npm` artifact, not a guest capability, and this
#    script asserts what the GUEST can do.
check_browser() {
  local chromium
  chromium=$(ls -d "$HOME"/.cache/ms-playwright/chromium_headless_shell-*/chrome-linux64/headless_shell \
                   "$HOME"/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell \
                   2>/dev/null | head -1)
  if [[ -z "$chromium" ]]; then
    echo "      (no chromium downloaded; run: cd frontend && npx playwright install chromium)"
    return 0
  fi
  "$chromium" --headless --no-sandbox --version
}
check "a headless browser launches (playwright system libs)" check_browser

echo "=== $PASS passed, $FAIL failed ==="
[[ "$FAIL" -eq 0 ]]
