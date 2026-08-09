# Phase 8 / P8.5 — VM Development Environment & Container Acceptance Runs

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the devcontainer with a libvirt/KVM guest that can nest containers, then build and validate the `ContainerEnvironment` adapter against real podman and real docker.

**Architecture:** Stage 1 provisions an Ubuntu 24.04 guest from checked-in cloud-init; `infra/dev-vm/verify.sh` proves the six capabilities the devcontainer lacks. Stage 2 implements `ProjectEnvironment` as a container adapter whose binary is configuration, tested against live containers. The stages are separated by a hard gate: **no Stage 2 task begins until `verify.sh` is green in the guest.**

**Tech Stack:** libvirt/KVM, cloud-init, Ubuntu 24.04, podman + docker, Python 3.11, pytest, structlog.

**Spec:** `docs/superpowers/specs/2026-08-08-phase-8-5-vm-development-environment-design.md`

## Global Constraints

- Python 3.11; `mypy agent_orchestrator` must pass with **zero errors and no exclude list**.
- `ruff check agent_orchestrator tests --fix`; line length 100.
- `from __future__ import annotations` at the top of every new Python module.
- **Never** `print()` or stdlib `logging` — `structlog` only, with namespaced action-oriented event names (`log.info("acceptance.container_started", ...)`).
- `verify()` **MUST NOT raise**, ever. Any failure returns `AcceptanceVerdict(outcome="errored", ...)`.
- The acceptance verdict is **advisory** — it never blocks goal promotion or the publication gate.
- The container binary is **configuration, never hardcoded**. No string literal `"docker"` in adapter logic.
- The operator authors `EnvironmentSpec`, never a model.
- Domain stays FROZEN. Nothing in this plan touches `agent_orchestrator/domain/`.
- VM sizing: 4 vCPU, 8 GiB RAM, 40 GiB sparse qcow2.
- No state migration. Credentials are rotated deliberately.

---

## Execution status — resume here

**Last updated 2026-08-08, on branch `phase-8-5-container-environment`.**

Stage 1 authoring is COMPLETE and reviewed. Tasks 4 onward are unstarted.

| Task | State | Commits |
|---|---|---|
| 1 — cloud-init | ✅ complete, review clean | `5ea8cb6` |
| 2 — lifecycle scripts | ✅ complete, 1 fix round | `0187a8a`, `4822404` |
| 3 — capability proof | ✅ complete, 2 fix rounds | `277c760`, `4d75740`, `04e892f` |
| final review fix wave | ✅ 6/6 addressed | `fe50dda` |
| 4 — provision & bootstrap | ⏸ **NEXT** — needs libvirt/KVM on the host | — |
| 5 — retire devcontainer | ⏸ after the gate | — |
| 6–11 — the adapter | ⏸ behind the gate | — |

**Verified in the devcontainer:** both scripts `bash -n` clean, cloud-init YAML
parses, and `verify.sh` runs RED (`0 passed, 7 failed`, exit 1) — which is the
required evidence, not a failure. See Task 3.

### Defects found and fixed during Stage 1

All five originated in this plan's own code blocks, not in transcription. Each
is fixed in both the script and the plan text, so a re-run cannot reintroduce
them.

1. `create-vm.sh` read `$IMAGE_DIR` for the free-space guard **before**
   `mkdir -p` created it — on a fresh host `df` failed, `|| echo 0` set
   `AVAIL_GB=0`, and provisioning was impossible.
2. `verify.sh` check 5 used `docker run --pid=private` — podman-only
   vocabulary that Docker rejects, so the gate could never open.
3. Container checks failed on `/dev/net/tun` during tap setup before reaching
   the PID/cgroup wall they test; fixed with `--network=none`.
4. `IMAGE_DIR` defaulted under `$HOME` while the domain is created on
   `qemu:///system` — Ubuntu 24.04's `HOME_MODE 0700` blocks `libvirt-qemu`
   traversal, failing the domain start **after** a 40 GiB write.
5. `verify.sh` check 3 ran `mkdir /sys/fs/cgroup/...` as unprivileged `dev`;
   the cgroup2 root is `755 root:root`, so it always returned `EACCES` — a
   permanent false red.

### Carried findings — triage at the final whole-branch review

- **Minor, deferred:** `/etc/profile.d/aipom.sh` sets `ORCHESTRATOR_HOME` only
  for login shells. A non-interactive `ssh host 'cmd'` will not see it. Revisit
  if a Stage 2 step runs remote commands non-interactively.
- **Minor, deferred:** `verify.sh`'s AppArmor comment lists checks 1/2/4/7 as
  userns-creating and omits check 5.
- **Recommendation, operator's call:** on a host with ~55 GiB free, `VM_DISK_GB`
  30 leaves a wider margin than the default 40. The qcow2 is sparse, so 40 is a
  ceiling (~15 GiB realistic use), not a reservation.

### If `verify.sh` is red in the guest

Check `kernel.apparmor_restrict_unprivileged_userns` before suspecting the
kernel — Ubuntu 24.04 ships it enabled, and it fails in a way that looks exactly
like a capability wall.

---

# STAGE 1 — The VM

## Task 1: cloud-init guest definition

**Files:**
- Create: `infra/dev-vm/cloud-init/user-data.yaml`
- Create: `infra/dev-vm/cloud-init/meta-data.yaml`

**Interfaces:**
- Consumes: nothing.
- Produces: a cloud-init datasource consumed by `create-vm.sh` (Task 2). Guest user is `dev`, home `/home/dev`, repo clone target `/home/dev/agent-orchestrator`.

- [ ] **Step 1: Create the meta-data file**

`infra/dev-vm/cloud-init/meta-data.yaml`:

```yaml
instance-id: aipom-dev-01
local-hostname: aipom-dev
```

- [ ] **Step 2: Create the user-data file**

`infra/dev-vm/cloud-init/user-data.yaml`:

```yaml
#cloud-config
users:
  - name: dev
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    lock_passwd: true
    ssh_authorized_keys:
      - SSH_PUBKEY_PLACEHOLDER

package_update: true
packages:
  - git
  - curl
  - build-essential
  - python3
  - python3-pip
  - python3-venv
  - podman
  - buildah
  - crun
  - docker.io
  - bubblewrap
  - uidmap
  - slirp4netns
  - fuse-overlayfs
  - jq

write_files:
  - path: /etc/profile.d/aipom.sh
    content: |
      export ORCHESTRATOR_HOME="$HOME/.orchestrator"

runcmd:
  - usermod -aG docker dev
  - grep -q '^dev:' /etc/subuid || usermod --add-subuids 100000-165535 --add-subgids 100000-165535 dev
  - systemctl enable --now docker
  - loginctl enable-linger dev
  - curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
  - curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  - apt-get install -y nodejs
  - npm install -g @anthropic-ai/claude-code @openai/codex
  - su - dev -c 'mkdir -p ~/.orchestrator'
```

`SSH_PUBKEY_PLACEHOLDER` is substituted by `create-vm.sh`; it is a build-time
token, not an unfilled plan step.

Note: `pi` is not published to npm under a stable public name in this
repository's tooling — it is installed by the maintainer post-boot, and Task 4
records that as a manual bootstrap step rather than pretending cloud-init does it.

- [ ] **Step 3: Verify the YAML parses**

Run: `python3 -c "import yaml,sys; yaml.safe_load(open('infra/dev-vm/cloud-init/user-data.yaml')); yaml.safe_load(open('infra/dev-vm/cloud-init/meta-data.yaml')); print('YAML OK')"`
Expected: `YAML OK`

- [ ] **Step 4: Commit**

```bash
git add infra/dev-vm/cloud-init/
git commit -m "feat(dev-vm): cloud-init definition for the aipom-dev guest"
```

---

## Task 2: VM lifecycle scripts

**Files:**
- Create: `infra/dev-vm/create-vm.sh`
- Create: `infra/dev-vm/Makefile`

**Interfaces:**
- Consumes: `cloud-init/user-data.yaml`, `cloud-init/meta-data.yaml` (Task 1).
- Produces: a libvirt domain named `aipom-dev`. `make ssh` opens a shell as `dev`. Consumed by Task 4.

- [ ] **Step 1: Write create-vm.sh**

`infra/dev-vm/create-vm.sh`:

```bash
#!/usr/bin/env bash
# Idempotent provisioning of the aipom-dev guest. Re-running when the domain
# already exists is a no-op, not an error.
set -euo pipefail

VM_NAME="${VM_NAME:-aipom-dev}"
VM_VCPUS="${VM_VCPUS:-4}"
VM_MEMORY_MB="${VM_MEMORY_MB:-8192}"
VM_DISK_GB="${VM_DISK_GB:-40}"
IMAGE_DIR="${IMAGE_DIR:-/var/lib/libvirt/images}"
BASE_URL="https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
BASE_IMG="$IMAGE_DIR/noble-server-cloudimg-amd64.img"
VM_DISK="$IMAGE_DIR/$VM_NAME.qcow2"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519.pub}"

if virsh --connect qemu:///system dominfo "$VM_NAME" >/dev/null 2>&1; then
  echo "Domain $VM_NAME already exists — nothing to do."
  exit 0
fi

if [[ ! -f "$SSH_KEY" ]]; then
  echo "ERROR: no SSH public key at $SSH_KEY. Generate one with:" >&2
  echo "  ssh-keygen -t ed25519 -N '' -f ${SSH_KEY%.pub}" >&2
  exit 1
fi

# qemu:///system runs QEMU as libvirt-qemu:kvm, which cannot traverse a
# user's $HOME (0700 on Ubuntu 24.04). /var/lib/libvirt/images is the
# libvirt-managed pool: root-owned and world-traversable, and libvirtd's
# dynamic_ownership relabels the disk file to libvirt-qemu:kvm at start.
# The directory must exist before df can measure it (and before the image
# lands in it) — create it before the free-space guard reads it.
sudo mkdir -p "$IMAGE_DIR"

# Disk is the binding constraint on this host: refuse rather than fill it.
# The directory is root-owned but world-traversable, so df needs no sudo.
AVAIL_GB="$(df -BG --output=avail "$IMAGE_DIR" 2>/dev/null | tail -1 | tr -dc '0-9')" || true
if [[ -z "$AVAIL_GB" ]]; then
  echo "ERROR: could not determine free space in $IMAGE_DIR (df failed)." >&2
  exit 1
fi
if [[ "$AVAIL_GB" -lt $((VM_DISK_GB + 5)) ]]; then
  echo "ERROR: need $((VM_DISK_GB + 5))G free in $IMAGE_DIR, have ${AVAIL_GB}G." >&2
  echo "Prune host images first, or lower VM_DISK_GB." >&2
  exit 1
fi

# Download to a .part file so an interrupted transfer can never be mistaken
# for a complete base image on re-run.
if [[ ! -f "$BASE_IMG" ]]; then
  sudo curl -fL --progress-bar -o "$BASE_IMG.part" "$BASE_URL"
  sudo mv "$BASE_IMG.part" "$BASE_IMG"
fi

sudo cp --reflink=auto "$BASE_IMG" "$VM_DISK"
sudo qemu-img resize "$VM_DISK" "${VM_DISK_GB}G"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
sed "s|SSH_PUBKEY_PLACEHOLDER|$(cat "$SSH_KEY")|" \
  "$HERE/cloud-init/user-data.yaml" > "$WORK/user-data"
cp "$HERE/cloud-init/meta-data.yaml" "$WORK/meta-data"

virt-install \
  --connect qemu:///system \
  --name "$VM_NAME" \
  --memory "$VM_MEMORY_MB" \
  --vcpus "$VM_VCPUS" \
  --cpu host-passthrough \
  --disk "path=$VM_DISK,format=qcow2,bus=virtio" \
  --cloud-init "user-data=$WORK/user-data,meta-data=$WORK/meta-data" \
  --os-variant ubuntu24.04 \
  --network network=default,model=virtio \
  --graphics none \
  --import \
  --noautoconsole

echo "Provisioned $VM_NAME. Wait for cloud-init, then: make -C infra/dev-vm ssh"
```

- [ ] **Step 2: Write the Makefile**

`infra/dev-vm/Makefile`:

```make
VM_NAME ?= aipom-dev
VIRSH := virsh --connect qemu:///system

.PHONY: up ip ssh status destroy verify

up:
	./create-vm.sh

ip:
	@$(VIRSH) domifaddr $(VM_NAME) | awk '/ipv4/ {split($$4,a,"/"); print a[1]}'

ssh:
	@state="$$($(VIRSH) domstate $(VM_NAME) 2>/dev/null)"; \
	if [ "$$state" != "running" ]; then \
	  echo "ERROR: $(VM_NAME) is not running (state: $${state:-absent}). Run 'make up' first." >&2; \
	  exit 1; \
	fi; \
	vmip="$$($(MAKE) -s ip)"; \
	if [ -z "$$vmip" ]; then \
	  echo "ERROR: $(VM_NAME) is running but has no DHCP lease yet. Wait a few seconds and retry." >&2; \
	  exit 1; \
	fi; \
	ssh -o StrictHostKeyChecking=accept-new dev@$$vmip

status:
	@$(VIRSH) dominfo $(VM_NAME)

verify:
	@state="$$($(VIRSH) domstate $(VM_NAME) 2>/dev/null)"; \
	if [ "$$state" != "running" ]; then \
	  echo "ERROR: $(VM_NAME) is not running (state: $${state:-absent}). Run 'make up' first." >&2; \
	  exit 1; \
	fi; \
	vmip="$$($(MAKE) -s ip)"; \
	if [ -z "$$vmip" ]; then \
	  echo "ERROR: $(VM_NAME) is running but has no DHCP lease yet. Wait a few seconds and retry." >&2; \
	  exit 1; \
	fi; \
	scp -o StrictHostKeyChecking=accept-new verify.sh dev@$$vmip:/tmp/verify.sh; \
	ssh dev@$$vmip 'bash /tmp/verify.sh'

destroy:
	-$(VIRSH) destroy $(VM_NAME)
	$(VIRSH) undefine $(VM_NAME) --remove-all-storage
```

- [ ] **Step 3: Make the script executable and shell-check it**

Run: `chmod +x infra/dev-vm/create-vm.sh && bash -n infra/dev-vm/create-vm.sh && echo "SYNTAX OK"`
Expected: `SYNTAX OK`

- [ ] **Step 4: Verify the guard fires without a key**

Run: `SSH_KEY=/nonexistent bash infra/dev-vm/create-vm.sh; echo "exit=$?"`
Expected: the `no SSH public key` error and `exit=1` (proves the guard works before any disk is touched).

- [ ] **Step 5: Commit**

```bash
git add infra/dev-vm/create-vm.sh infra/dev-vm/Makefile
git commit -m "feat(dev-vm): idempotent virt-install provisioning and lifecycle targets"
```

---

## Task 3: The capability proof

**Files:**
- Create: `infra/dev-vm/verify.sh`

**Interfaces:**
- Consumes: a booted guest (Task 2).
- Produces: **the gate for Stage 2.** Exit 0 means every Stage 2 task may proceed. Consumed by `make verify` and by Task 4.

- [ ] **Step 1: Write verify.sh**

`infra/dev-vm/verify.sh`:

```bash
#!/usr/bin/env bash
# Asserts exactly the six capabilities the devcontainer could not provide.
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

echo "=== aipom-dev capability proof ==="

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
  bash -c 'sudo mkdir -p /sys/fs/cgroup/aipom-probe && sudo rmdir /sys/fs/cgroup/aipom-probe'
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
```

- [ ] **Step 2: Make executable and syntax-check**

Run: `chmod +x infra/dev-vm/verify.sh && bash -n infra/dev-vm/verify.sh && echo "SYNTAX OK"`
Expected: `SYNTAX OK`

- [ ] **Step 3: Prove it fails where it should**

Run it **in the current devcontainer**, which must fail — a proof script that
passes in a known-incapable environment is worthless.

Run: `bash infra/dev-vm/verify.sh; echo "exit=$?"`
Expected: several `FAIL` lines (notably `bwrap mounts a fresh /proc` and the podman checks) and a non-zero exit.

- [ ] **Step 4: Commit**

```bash
git add infra/dev-vm/verify.sh
git commit -m "test(dev-vm): capability proof for nested containers, red in the devcontainer"
```

---

## Task 4: Provision the guest and bootstrap it

**Files:**
- Create: `infra/dev-vm/README.md`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: **a green `verify.sh` run — the Stage 2 gate.**

This task runs on the maintainer's host, not inside the devcontainer.

- [ ] **Step 1: Free disk space and record the measurement**

```bash
df -h /
podman system prune -a -f 2>/dev/null || true
docker system prune -a -f 2>/dev/null || true
df -h /
```

Record before/after. If less than 45 GiB is free, lower `VM_DISK_GB` rather than proceeding.

- [ ] **Step 2: Install host virtualization packages**

```bash
sudo apt-get install -y qemu-kvm libvirt-daemon-system virtinst qemu-utils
sudo usermod -aG libvirt,kvm "$USER"
newgrp libvirt
sudo virsh net-start default 2>/dev/null || true
sudo virsh net-autostart default
```

- [ ] **Step 3: Provision**

Run: `make -C infra/dev-vm up`
Expected: `Provisioned aipom-dev`. Then wait for cloud-init: `make -C infra/dev-vm ssh` and run `cloud-init status --wait`.

- [ ] **Step 4: Run the gate**

Run: `make -C infra/dev-vm verify`
Expected: `=== 7 passed, 0 failed ===` and exit 0.

**If any check fails, stop.** Fix the guest or the cloud-init and re-run. Do not begin Stage 2 with a red gate.

- [ ] **Step 5: Bootstrap the orchestrator in the guest**

```bash
git clone <repo-url> ~/agent-orchestrator && cd ~/agent-orchestrator/backend
uv pip install --system -e '.[dev]'
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
# export the printed value as ORCHESTRATOR_MASTER_KEY in ~/.bashrc, then:
python -m agent_orchestrator.infra.cli.main db upgrade
python -m agent_orchestrator.infra.cli.main seed demo
```

Then, in order:
1. Install the `pi` CLI (not covered by cloud-init) and authenticate `claude` and `codex`.
2. Re-enter the OpenRouter API key.
3. **Re-point the reasoner and all six agents at their free OpenRouter models** — `seed demo` overwrites this, so it is its own step.
4. Regenerate the demo repo: `bash demos/static-site-v1/scripts/materialize.sh`.

- [ ] **Step 6: Confirm the suite passes in the guest**

Run: `cd ~/agent-orchestrator/backend && pytest -m "not integration" -q`
Expected: all pass.

- [ ] **Step 7: Write the README**

`infra/dev-vm/README.md` must contain: the host prerequisites from Step 2, the
`make up` / `ssh` / `verify` / `destroy` lifecycle, the bootstrap sequence from
Step 5, and a **Threat model** section stating that the VM boundary — not
bubblewrap — is what now contains agent-written code, that the guest is cattle
rather than a pet, and that all durable state lives in `~/.orchestrator`.

- [ ] **Step 8: Commit**

```bash
git add infra/dev-vm/README.md
git commit -m "docs(dev-vm): setup, lifecycle and threat model for the aipom-dev guest"
```

---

## Task 5: Retire the devcontainer and correct the record

**Files:**
- Delete: `.devcontainer/` (all five files)
- Modify: `CLAUDE.md`
- Modify: `ROADMAP.md:1293-1331`
- Modify: `docs/decisions/decision-log.md`

**Interfaces:**
- Consumes: a green gate (Task 4).
- Produces: nothing code-level.

- [ ] **Step 1: Confirm no CI depends on the devcontainer**

Run: `grep -rn "devcontainer" .github/ backend/ frontend/ 2>/dev/null | grep -v node_modules; echo "exit=$?"`
Expected: no matches (`exit=1`).

- [ ] **Step 2: Delete it**

```bash
git rm -r .devcontainer/
```

- [ ] **Step 3: Correct ROADMAP.md**

Replace the *Containerization is unavailable in the development environment* section. It must now state:
- The re-test on 2026-08-08 found **two walls that deadlock each other**, not one final wall: masked `/proc` forbids a fresh procfs and therefore a private PID namespace; read-only `/sys/fs/cgroup` forces `--cgroups=disabled`, which itself forces a private PID namespace.
- A hand-rolled OCI bundle **did** run a real container, so the honest statement is that the devcontainer could run containers but not PID-isolated ones.
- The VM resolves both. Move P8.5 from ⏸ to its current state and drop the "pending a container-capable environment" language.

Keep the two design consequences that survive unchanged — the adapter must not hardcode `docker`, and "the binary exists but containers do not work here" is a real state returning `errored`.

- [ ] **Step 4: Update CLAUDE.md**

Replace devcontainer references with the guest workflow (`make -C infra/dev-vm up`, then SSH). Add `infra/dev-vm/` to the Repository Structure tree.

- [ ] **Step 5: Add the decision-log entry**

Append an entry dated 2026-08-08: the development environment moves from a hardened devcontainer to a libvirt/KVM guest, because the environment must be simultaneously privileged enough to nest containers and isolated enough to contain agent code — which a container cannot be, and a VM is by construction. State explicitly: **no domain un-freeze; nothing here touches the domain.**

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(dev-env): retire the devcontainer for the KVM guest, correct the roadmap"
```

---

# 🚦 GATE

**`infra/dev-vm/verify.sh` must exit 0 in the guest.** Every Stage 2 task runs
inside the guest. Do not proceed on a red gate.

---

# STAGE 2 — The container adapter

## Task 6: The container binary is configuration

**Files:**
- Modify: `backend/agent_orchestrator/infra/environment/spec.py`
- Modify: `backend/pyproject.toml`
- Test: `backend/tests/unit/test_environment_spec.py`

**Interfaces:**
- Consumes: `SqliteConfigStore` from `infra/db/reference_repos.py`.
- Produces: `CONTAINER_BINARY_KEY: str = "environment.container_binary"` and `read_container_binary(config_store: SqliteConfigStore) -> str`, both consumed by Tasks 7–11.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_environment_spec.py`:

```python
from __future__ import annotations

from agent_orchestrator.infra.environment.spec import (
    CONTAINER_BINARY_KEY,
    read_container_binary,
)


class _Store:
    def __init__(self, values: dict[tuple[str, str], str]) -> None:
        self._values = values

    def get(self, scope: str, key: str) -> str | None:
        return self._values.get((scope, key))


def test_container_binary_defaults_to_docker() -> None:
    assert read_container_binary(_Store({})) == "docker"


def test_container_binary_is_configuration() -> None:
    store = _Store({("orchestrator", CONTAINER_BINARY_KEY): "podman"})
    assert read_container_binary(store) == "podman"


def test_blank_container_binary_degrades_to_the_default() -> None:
    store = _Store({("orchestrator", CONTAINER_BINARY_KEY): "   "})
    assert read_container_binary(store) == "docker"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && pytest tests/unit/test_environment_spec.py -q`
Expected: FAIL — `ImportError: cannot import name 'CONTAINER_BINARY_KEY'`.

- [ ] **Step 3: Implement**

Append to `backend/agent_orchestrator/infra/environment/spec.py`:

```python
# Orchestrator-scoped, not project-scoped: which container CLI exists is a
# property of the machine, not of the project being built.
CONTAINER_BINARY_KEY = "environment.container_binary"

_DEFAULT_CONTAINER_BINARY = "docker"


def read_container_binary(config_store: SqliteConfigStore) -> str:
    """The container CLI to shell out to.

    Configuration rather than a hardcoded `docker`: podman, colima and rancher
    are CLI-compatible, and stranding those operators buys nothing.
    """
    configured = config_store.get("orchestrator", CONTAINER_BINARY_KEY)
    if not configured or not configured.strip():
        return _DEFAULT_CONTAINER_BINARY
    return configured.strip()
```

- [ ] **Step 4: Run the tests**

Run: `cd backend && pytest tests/unit/test_environment_spec.py -q`
Expected: 3 passed.

- [ ] **Step 5: Add the `container` marker**

In `backend/pyproject.toml`, add to `markers`:

```toml
    "container: needs a working container runtime; deselect with -m 'not container'",
```

- [ ] **Step 6: Commit**

```bash
git add backend/agent_orchestrator/infra/environment/spec.py backend/tests/unit/test_environment_spec.py backend/pyproject.toml
git commit -m "feat(environment): the container binary is configuration, not a hardcoded docker"
```

---

## Task 7: ContainerEnvironment — the happy path against a real container

**Files:**
- Create: `backend/agent_orchestrator/infra/environment/container_environment.py`
- Test: `backend/tests/integration/test_container_environment.py`

**Interfaces:**
- Consumes: `read_container_binary` (Task 6); `AcceptanceVerdict`, `EnvironmentSpec`, `ProjectEnvironment` from `app/environment_port.py`.
- Produces: `ContainerEnvironment(binary: str, workspace_root: Path | None = None)` with `verify(repo: Path, ref: str, spec: EnvironmentSpec | None) -> AcceptanceVerdict`. Consumed by Tasks 8–11.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/integration/test_container_environment.py`:

```python
"""ContainerEnvironment against REAL containers. See P8.5: fakes lose exactly
the behaviour this adapter exists to prove."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from agent_orchestrator.app.environment_port import EnvironmentSpec
from agent_orchestrator.infra.environment.container_environment import ContainerEnvironment

pytestmark = [pytest.mark.integration, pytest.mark.container]

BINARIES = [b for b in ("docker", "podman") if shutil.which(b)]
IMAGE = "docker.io/library/alpine:3.20"


@pytest.fixture(params=BINARIES or ["docker"])
def binary(request: pytest.FixtureRequest) -> str:
    if not BINARIES:
        pytest.skip("no container runtime on PATH")
    return str(request.param)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repo with one commit, so `ref` means something."""
    path = tmp_path / "project"
    path.mkdir()
    run = lambda *a: subprocess.run(a, cwd=path, check=True, capture_output=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "t")
    (path / "marker.txt").write_text("from-the-repo\n", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "seed")
    return path


def test_a_passing_scenario_reports_passed(binary: str, repo: Path) -> None:
    env = ContainerEnvironment(binary=binary)
    spec = EnvironmentSpec(
        image=IMAGE,
        command="sleep 300",
        scenario=["cat /app/marker.txt"],
        startup_timeout_seconds=60,
    )
    verdict = env.verify(repo, "HEAD", spec)
    assert verdict.outcome == "passed", verdict.detail
    assert "from-the-repo" in verdict.detail


def test_a_failing_scenario_reports_failed(binary: str, repo: Path) -> None:
    env = ContainerEnvironment(binary=binary)
    spec = EnvironmentSpec(
        image=IMAGE,
        command="sleep 300",
        scenario=["test -f /app/does-not-exist"],
        startup_timeout_seconds=60,
    )
    verdict = env.verify(repo, "HEAD", spec)
    assert verdict.outcome == "failed"


def test_no_spec_is_skipped_not_passed(binary: str, repo: Path) -> None:
    verdict = ContainerEnvironment(binary=binary).verify(repo, "HEAD", None)
    assert verdict.outcome == "skipped"
    assert verdict.is_signal is False
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && pytest tests/integration/test_container_environment.py -q`
Expected: FAIL — `ModuleNotFoundError: ... container_environment`.

- [ ] **Step 3: Implement**

Create `backend/agent_orchestrator/infra/environment/container_environment.py`:

```python
"""Boot a cycle's tree in a real container and check the application works.

The container binary is configuration (`environment.container_binary`): podman,
colima and rancher are CLI-compatible with docker for everything used here.

`verify()` MUST NOT raise. An acceptance run is advisory, and a crash inside it
must never take down the promotion or the publication gate it was observing.
"""

from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from pathlib import Path

import structlog

from agent_orchestrator.app.environment_port import AcceptanceVerdict, EnvironmentSpec

log = structlog.get_logger(__name__)

_MOUNT_POINT = "/app"


class ContainerEnvironment:
    def __init__(self, binary: str, workspace_root: Path | None = None) -> None:
        self._binary = binary
        self._workspace_root = workspace_root

    def verify(
        self, repo: Path, ref: str, spec: EnvironmentSpec | None
    ) -> AcceptanceVerdict:
        if spec is None:
            return AcceptanceVerdict(
                outcome="skipped",
                summary="No project environment is configured, so the application was not booted.",
            )
        started = time.monotonic()
        try:
            return self._run(repo, ref, spec, started)
        except Exception as exc:  # verify() must not raise — see the docstring.
            log.warning("acceptance.errored", error=str(exc), ref=ref)
            return AcceptanceVerdict(
                outcome="errored",
                summary=f"The acceptance run could not complete: {exc}",
                duration_seconds=time.monotonic() - started,
            )

    def _run(
        self, repo: Path, ref: str, spec: EnvironmentSpec, started: float
    ) -> AcceptanceVerdict:
        if shutil.which(self._binary) is None:
            return AcceptanceVerdict(
                outcome="errored",
                summary=f"`{self._binary}` is not installed, so the application was not booted.",
                detail=(
                    f"Install {self._binary}, or set the `environment.container_binary` "
                    f"config key to a container CLI that is on PATH."
                ),
                duration_seconds=time.monotonic() - started,
            )

        name = f"aipom-acceptance-{uuid.uuid4().hex[:12]}"
        with _checkout(repo, ref, self._workspace_root) as tree:
            try:
                self._start(name, tree, spec)
            except _CommandFailed as exc:
                return AcceptanceVerdict(
                    outcome="errored",
                    summary="The container did not start.",
                    detail=exc.output,
                    duration_seconds=time.monotonic() - started,
                )
            try:
                return self._observe(name, spec, started)
            finally:
                # Teardown on every path, including failure.
                self._exec([self._binary, "rm", "-f", name], timeout=60, check=False)

    def _start(self, name: str, tree: Path, spec: EnvironmentSpec) -> None:
        cmd = [
            self._binary, "run", "-d", "--name", name,
            "-v", f"{tree}:{_MOUNT_POINT}",
            "-w", _MOUNT_POINT,
        ]
        if spec.port:
            cmd += ["-p", f"{spec.port}:{spec.port}"]
        cmd.append(spec.image)
        if spec.command:
            cmd += ["sh", "-c", spec.command]
        result = self._exec(cmd, timeout=spec.startup_timeout_seconds)
        log.info("acceptance.container_started", container=name, image=spec.image)
        _ = result

    def _observe(
        self, name: str, spec: EnvironmentSpec, started: float
    ) -> AcceptanceVerdict:
        if spec.healthcheck and not self._await_health(name, spec):
            return AcceptanceVerdict(
                outcome="failed",
                summary="The application did not become healthy before the timeout.",
                detail=f"Healthcheck `{spec.healthcheck}` never succeeded "
                       f"within {spec.startup_timeout_seconds}s.",
                duration_seconds=time.monotonic() - started,
            )

        transcript: list[str] = []
        for step in spec.scenario:
            proc = self._exec(
                [self._binary, "exec", name, "sh", "-c", step],
                timeout=spec.startup_timeout_seconds,
                check=False,
            )
            output = (proc.stdout + proc.stderr).strip()
            transcript.append(f"$ {step}\n{output}")
            if proc.returncode != 0:
                return AcceptanceVerdict(
                    outcome="failed",
                    summary=f"Scenario step failed: {step}",
                    detail="\n".join(transcript),
                    duration_seconds=time.monotonic() - started,
                )

        return AcceptanceVerdict(
            outcome="passed",
            summary=f"The application booted and all {len(spec.scenario)} scenario "
                    f"step(s) succeeded.",
            detail="\n".join(transcript),
            duration_seconds=time.monotonic() - started,
        )

    def _await_health(self, name: str, spec: EnvironmentSpec) -> bool:
        assert spec.healthcheck is not None
        deadline = time.monotonic() + spec.startup_timeout_seconds
        while time.monotonic() < deadline:
            proc = self._exec(
                [self._binary, "exec", name, "sh", "-c", spec.healthcheck],
                timeout=30,
                check=False,
            )
            if proc.returncode == 0:
                return True
            time.sleep(1.0)
        return False

    def _exec(
        self, cmd: list[str], timeout: int, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if check and proc.returncode != 0:
            raise _CommandFailed((proc.stdout + proc.stderr).strip())
        return proc


class _CommandFailed(Exception):
    def __init__(self, output: str) -> None:
        super().__init__(output)
        self.output = output
```

Add the checkout helper to the same module:

```python
import contextlib
import tempfile
from collections.abc import Iterator


@contextlib.contextmanager
def _checkout(repo: Path, ref: str, root: Path | None) -> Iterator[Path]:
    """A disposable worktree at `ref`, so the run sees exactly that commit and
    never the developer's dirty working tree."""
    with tempfile.TemporaryDirectory(dir=root) as tmp:
        tree = Path(tmp) / "tree"
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(tree), ref],
            cwd=repo, capture_output=True, text=True, check=True, timeout=120,
        )
        try:
            yield tree
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(tree)],
                cwd=repo, capture_output=True, text=True, check=False, timeout=120,
            )
```

Place the imports at the top of the module with the others.

- [ ] **Step 4: Run the tests**

Run: `cd backend && pytest tests/integration/test_container_environment.py -q -p no:xdist`
Expected: all pass, parametrized over every runtime on PATH.

- [ ] **Step 5: Commit**

```bash
git add backend/agent_orchestrator/infra/environment/container_environment.py backend/tests/integration/test_container_environment.py
git commit -m "feat(environment): ContainerEnvironment booting real containers"
```

---

## Task 8: Healthcheck, port and timeout behaviour

**Files:**
- Modify: `backend/tests/integration/test_container_environment.py`

**Interfaces:**
- Consumes: `ContainerEnvironment` (Task 7). No new production interfaces.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/integration/test_container_environment.py`:

```python
def test_a_healthcheck_that_never_passes_reports_failed(binary: str, repo: Path) -> None:
    env = ContainerEnvironment(binary=binary)
    spec = EnvironmentSpec(
        image=IMAGE,
        command="sleep 300",
        healthcheck="test -f /app/never-appears",
        scenario=["true"],
        startup_timeout_seconds=3,
    )
    verdict = env.verify(repo, "HEAD", spec)
    assert verdict.outcome == "failed"
    assert "healthy" in verdict.summary


def test_a_passing_healthcheck_lets_the_scenario_run(binary: str, repo: Path) -> None:
    env = ContainerEnvironment(binary=binary)
    spec = EnvironmentSpec(
        image=IMAGE,
        command="sleep 300",
        healthcheck="test -f /app/marker.txt",
        scenario=["echo scenario-ran"],
        startup_timeout_seconds=60,
    )
    verdict = env.verify(repo, "HEAD", spec)
    assert verdict.outcome == "passed"
    assert "scenario-ran" in verdict.detail


def test_the_run_sees_the_ref_not_the_working_tree(binary: str, repo: Path) -> None:
    """A dirty working tree must not leak into the acceptance run."""
    (repo / "marker.txt").write_text("DIRTY\n", encoding="utf-8")
    env = ContainerEnvironment(binary=binary)
    spec = EnvironmentSpec(
        image=IMAGE, command="sleep 300",
        scenario=["cat /app/marker.txt"], startup_timeout_seconds=60,
    )
    verdict = env.verify(repo, "HEAD", spec)
    assert verdict.outcome == "passed"
    assert "from-the-repo" in verdict.detail
    assert "DIRTY" not in verdict.detail


def test_no_container_survives_the_run(binary: str, repo: Path) -> None:
    """Teardown happens on the failure path too."""
    env = ContainerEnvironment(binary=binary)
    spec = EnvironmentSpec(
        image=IMAGE, command="sleep 300",
        scenario=["false"], startup_timeout_seconds=60,
    )
    assert env.verify(repo, "HEAD", spec).outcome == "failed"
    listing = subprocess.run(
        [binary, "ps", "-a", "--format", "{{.Names}}"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "aipom-acceptance-" not in listing
```

- [ ] **Step 2: Run them**

Run: `cd backend && pytest tests/integration/test_container_environment.py -q -p no:xdist`
Expected: all pass. If `test_the_run_sees_the_ref_not_the_working_tree` fails, the worktree checkout in Task 7 is wrong — fix it rather than the test.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/integration/test_container_environment.py
git commit -m "test(environment): healthcheck, ref isolation and teardown against real containers"
```

---

## Task 9: The paths a live daemon cannot stage

**Files:**
- Create: `backend/tests/integration/test_container_environment_failures.py`

**Interfaces:**
- Consumes: `ContainerEnvironment` (Task 7).

These two cases use a scripted CLI as **failure injection** — you cannot make a
real daemon vanish mid-run on demand. Every path that can be exercised for real
is exercised for real in Tasks 7 and 8.

- [ ] **Step 1: Write the failing tests**

```python
"""The two paths a live daemon cannot be made to take on demand: the binary is
absent, and the daemon refuses. Failure injection, not a substitute for the
real-container tests in test_container_environment.py."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from agent_orchestrator.app.environment_port import EnvironmentSpec
from agent_orchestrator.infra.environment.container_environment import ContainerEnvironment

pytestmark = pytest.mark.integration

SPEC = EnvironmentSpec(image="alpine:3.20", command="sleep 1", scenario=["true"])


def _scripted(tmp_path: Path, body: str) -> str:
    script = tmp_path / "faked-cli"
    script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def test_a_missing_binary_errors_with_an_actionable_message(tmp_path: Path) -> None:
    verdict = ContainerEnvironment(binary="definitely-not-installed").verify(
        tmp_path, "HEAD", SPEC
    )
    assert verdict.outcome == "errored"
    assert "not installed" in verdict.summary
    assert "environment.container_binary" in verdict.detail


def test_a_daemon_that_refuses_errors_rather_than_hanging(tmp_path: Path) -> None:
    cli = _scripted(tmp_path, 'echo "Cannot connect to the daemon" >&2; exit 1')
    verdict = ContainerEnvironment(binary=cli).verify(tmp_path, "HEAD", SPEC)
    assert verdict.outcome == "errored"


def test_verify_never_raises_even_on_a_nonsense_repo(tmp_path: Path) -> None:
    verdict = ContainerEnvironment(binary="echo").verify(
        tmp_path / "no-such-repo", "HEAD", SPEC
    )
    assert verdict.outcome == "errored"
```

- [ ] **Step 2: Run them**

Run: `cd backend && pytest tests/integration/test_container_environment_failures.py -q`
Expected: 3 passed. Each must return `errored` — none may raise.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/integration/test_container_environment_failures.py
git commit -m "test(environment): missing binary and refusing daemon return errored, never raise"
```

---

## Task 10: Wire it into the composition root

**Files:**
- Modify: `backend/agent_orchestrator/infra/container.py:202-209`
- Test: `backend/tests/unit/test_environment_selection.py`

**Interfaces:**
- Consumes: `ContainerEnvironment` (Task 7), `read_container_binary` (Task 6).
- Produces: `AppContainer.environment` returning `ContainerEnvironment` when `environment.mode` is `container`, else `NoEnvironment`.

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

from pathlib import Path

import pytest

from agent_orchestrator.infra.container import AppContainer
from agent_orchestrator.infra.db.models import Base
from agent_orchestrator.infra.environment.container_environment import ContainerEnvironment
from agent_orchestrator.infra.environment.no_environment import NoEnvironment


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """`AppContainer` has no `migrate()`; tests create the schema directly —
    the pattern in test_attempt_feedback.py and test_agent_runner_factory.py."""
    Base.metadata.create_all(AppContainer(orchestrator_home=tmp_path).engine)
    return tmp_path


def test_the_default_is_the_permanent_no_environment_fallback(home: Path) -> None:
    assert isinstance(AppContainer(orchestrator_home=home).environment, NoEnvironment)


def test_container_mode_selects_the_container_adapter(home: Path) -> None:
    AppContainer(orchestrator_home=home).config_store.set(
        "orchestrator", "environment.mode", "container"
    )
    # A fresh container: `environment` is a cached_property, so the one that
    # wrote the key would return its already-resolved adapter.
    assert isinstance(
        AppContainer(orchestrator_home=home).environment, ContainerEnvironment
    )
```

`config_store.set(scope, key, value)` and `Base.metadata.create_all(container.engine)`
are both verified against existing tests. Confirm the `Base` import path matches
`infra/db/models.py` in your tree before running.

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && pytest tests/unit/test_environment_selection.py -q`
Expected: FAIL on the second test — `NoEnvironment` is returned unconditionally.

- [ ] **Step 3: Implement**

Replace the `environment` property body in `container.py`:

```python
    @cached_property
    def environment(self) -> ProjectEnvironment:
        """The cycle acceptance run (P8.2/P8.5). `NoEnvironment` remains the
        PERMANENT fallback, like `NoSandbox` and `NoForge` — most projects are
        libraries and CLIs whose tests genuinely are the contract.

        `environment.mode = container` selects the real adapter; the container
        CLI itself is a separate key, because which runtime exists is a property
        of the machine and not of the project.
        """
        mode = (self.config_store.get("orchestrator", "environment.mode") or "").strip()
        if mode != "container":
            return NoEnvironment()
        return ContainerEnvironment(binary=read_container_binary(self.config_store))
```

Add the imports beside the existing environment imports:

```python
from agent_orchestrator.infra.environment.container_environment import ContainerEnvironment
from agent_orchestrator.infra.environment.spec import read_container_binary, read_environment_spec
```

- [ ] **Step 4: Run the tests**

Run: `cd backend && pytest tests/unit/test_environment_selection.py -q`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/agent_orchestrator/infra/container.py backend/tests/unit/test_environment_selection.py
git commit -m "feat(environment): select ContainerEnvironment via environment.mode"
```

---

## Task 11: Prove it end to end, then tell the truth about it

**Files:**
- Modify: `ROADMAP.md`
- Modify: `docs/architecture/capability-matrix.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Full verification in the guest**

```bash
cd ~/agent-orchestrator/backend
ruff check agent_orchestrator tests --fix
mypy agent_orchestrator
pytest -q
```

Expected: ruff clean, **mypy zero errors**, whole suite green. Record the output — the spec's exit criteria are evidence-based.

- [ ] **Step 2: A real acceptance run under both runtimes**

```bash
cd ~/agent-orchestrator/backend
pytest tests/integration/test_container_environment.py -v -p no:xdist
```

Expected: every test passes **twice** — once parametrized on `docker`, once on `podman`. That double pass is what converts "the binary is configuration" from a decision into a tested fact.

- [ ] **Step 3: Confirm the advisory contract still holds**

Run: `cd backend && pytest tests/integration/test_acceptance_run.py -q`
Expected: pass. A `failed` verdict must still block neither goal promotion nor the publication gate.

- [ ] **Step 4: Update the docs**

- `ROADMAP.md` — mark **P8.5 ✅ delivered**, with a Delivery status entry naming: the two config keys, `NoEnvironment` remaining the permanent fallback, both runtimes exercised, and the two failure-injection cases stated plainly as such.
- `docs/architecture/capability-matrix.md` — add the acceptance run with its config keys and where it is exposed.
- `CLAUDE.md` — document `environment.mode` and `environment.container_binary` beside the other config keys.

- [ ] **Step 5: Commit and open the PR**

```bash
git add -A
git commit -m "feat: Phase 8 / P8.5 — the container acceptance run on a VM dev environment"
git push -u origin phase-8-5-container-environment
gh pr create --title "feat: Phase 8 / P8.5 — VM dev environment and the container acceptance run" --body "..."
```

---

## Self-Review

**Spec coverage.** Architecture → Tasks 1–2, 4. Repository layout → Tasks 1–4. VM specification and the disk constraint → Task 2 (guard) and Task 4 (measurement). Bootstrap without migration → Task 4 Step 5. Verification's six checks → Task 3. P8.5 adapter → Tasks 6–10. The testing note on failure injection → Task 9. Documentation consequences → Tasks 5 and 11. Exit criteria → Task 11 Steps 1–3. **No gaps.**

**Placeholders.** `SSH_PUBKEY_PLACEHOLDER` is a substitution token consumed by `create-vm.sh`, not an unfilled step. The `gh pr create --body "..."` is written at PR time from the commits. Task 10 Step 1 flags that `AppContainer`'s config-store accessor should be matched to existing test usage rather than assumed — a deliberate instruction, not a gap.

**Type consistency.** `ContainerEnvironment(binary=..., workspace_root=...)` and `verify(repo, ref, spec)` match `ProjectEnvironment` in `app/environment_port.py`. `read_container_binary(config_store)` and `CONTAINER_BINARY_KEY` are named identically in Tasks 6, 10. `AcceptanceVerdict` fields (`outcome`, `summary`, `detail`, `duration_seconds`) match the frozen dataclass. `_MOUNT_POINT` is `/app` in both the adapter and every test.

**Known soft spot.** Task 3's verify.sh prints "7 passed" against six numbered capabilities because cgroup2 contributes two checks. Task 4 Step 4 expects 7 — correct, but worth not mistaking for a bug.
