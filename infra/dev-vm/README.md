# `aipom-dev` — the development guest

A libvirt/KVM virtual machine that replaces the old `.devcontainer/`. It exists
because the development environment has to be two things at once: **privileged
enough to nest containers** (podman and docker, with private PID namespaces and
a writable cgroup2 tree) and **isolated enough to contain agent-written code**.
A container cannot be both. A VM is both by construction.

The capability gate that licenses this is `verify.sh` — see [The gate](#the-gate).

---

## Host prerequisites

Everything here runs on the **host**, once.

```bash
sudo apt-get install -y qemu-kvm libvirt-daemon-system virtinst qemu-utils
sudo usermod -aG libvirt,kvm "$USER"
newgrp libvirt                       # or log out and back in
sudo virsh net-start default 2>/dev/null || true
sudo virsh net-autostart default
```

You also need:

- **An SSH public key** at `~/.ssh/id_ed25519.pub` (override with `SSH_KEY=`).
  `create-vm.sh` refuses to provision without one — cloud-init installs it as
  the `dev` user's only credential, since the account has `lock_passwd: true`.
- **Free disk in `/var/lib/libvirt/images`**: `VM_DISK_GB + 5`, so 45 GiB at
  the default. The script measures this and refuses rather than filling the
  disk. The qcow2 is sparse, so 40 GiB is a ceiling (~15 GiB realistic), not a
  reservation — on a tight host, `VM_DISK_GB=30 make up` is the honest fix.
- **Hardware virtualization** enabled in firmware (`kvm-ok` confirms).

`IMAGE_DIR` deliberately defaults to `/var/lib/libvirt/images`, **not** to
anywhere under `$HOME`. The domain runs on `qemu:///system`, where QEMU is
`libvirt-qemu:kvm`, and Ubuntu 24.04's `HOME_MODE 0700` blocks that user from
traversing your home directory — the domain would fail to start *after* a
40 GiB write.

### Tunables

| Variable | Default | Notes |
|---|---|---|
| `VM_NAME` | `aipom-dev` | Also the guest hostname. |
| `VM_VCPUS` | `4` | |
| `VM_MEMORY_MB` | `8192` | |
| `VM_DISK_GB` | `40` | Sparse ceiling. |
| `IMAGE_DIR` | `/var/lib/libvirt/images` | See above before changing. |
| `SSH_KEY` | `~/.ssh/id_ed25519.pub` | |

---

## Lifecycle

All targets run from the host. `make -C infra/dev-vm <target>`.

| Target | What it does |
|---|---|
| `up` | Provisions the domain via `virt-install`. **Idempotent** — if the domain already exists it prints `already exists` and exits 0 **without booting it**. |
| `start` | Boots an existing but powered-off domain. |
| `ip` | Prints the guest's DHCP address. |
| `ssh` | Opens a shell as `dev`. Errors clearly if the domain is down or has no lease yet. |
| `status` | `virsh dominfo`. |
| `verify` | Copies `verify.sh` into the guest and runs the capability gate. |
| `destroy` | **Destructive.** Powers off, undefines, and `--remove-all-storage`. Everything in the guest is gone. |

`up` and `start` are not interchangeable, and the guest lands in the gap
routinely: `virt-install --cloud-init` sets `on_reboot=destroy` for the seed
boot, so the first in-guest `reboot` powers the domain **off** rather than
restarting it. `up` will then cheerfully report "already exists" and boot
nothing. If `ssh` says the domain is not running, you want `start`.

### First boot

```bash
make -C infra/dev-vm up
# wait, then:
make -C infra/dev-vm ssh
cloud-init status --wait
```

Cloud-init installs git, build-essential, python3, podman, buildah, crun,
docker.io, bubblewrap, uidmap, slirp4netns, fuse-overlayfs and jq; adds `dev` to
`docker`; allocates subuid/subgid ranges for rootless podman; enables lingering;
installs `uv` into `/usr/local/bin`; installs Node 22 and the `claude` and
`codex` CLIs globally; and writes the userns sysctl (see fact 5).

---

## The gate

```bash
make -C infra/dev-vm verify
```

`verify.sh` asserts the capabilities the devcontainer could not provide, plus
one added 2026-08-10 for Phase 9: that a headless browser actually launches.
Playwright ships the browser binary but not the system libraries it links
against, so a guest missing them turns every browser spec red with
`libatk-1.0.so.0: cannot open shared object file` — which reads like a broken
test suite and is a missing package. That check skips (rather than fails) when
no browser has been downloaded yet, because the binary is a per-checkout `npm`
artifact and this script asserts what the GUEST can do.

It is the artifact the environment work is judged by. Expected output:

```text
=== aipom-dev capability proof ===
PASS  bwrap mounts a fresh /proc
PASS  fresh procfs in a private PID namespace
PASS  cgroup2 is writable
PASS  cgroup2 mounts in a user namespace
PASS  podman runs with cgroups and a private PID namespace
PASS  docker runs with a private PID namespace
PASS  rootless podman runs with full isolation
PASS  a headless browser launches (playwright system libs)
=== 8 passed, 0 failed ===
```

Verified on Ubuntu 24.04.4, kernel **6.8.0-137**, re-run after a kernel upgrade
and a full power cycle — so the sysctl is proven to apply at boot, not only in
a session where someone set it by hand.

**A red gate does not license Stage 2 work.** Before suspecting the kernel, read
guest fact 5.

---

## Bootstrap sequence

Inside the guest, as `dev`.

```bash
git clone <repo-url> ~/agent-orchestrator
cd ~/agent-orchestrator/backend
```

Cloud-init installs git but configures no identity, and the guest's default
(`dev@aipom-dev.(none)`) is not auto-detectable — the first `git commit` fails
with `Author identity unknown`. Set it before you need it:

```bash
git config --global user.name "..." && git config --global user.email "..."
```

**Install.** `uv pip install --system -e '.[dev]'` fails here — see fact 1. Use
the same command CI uses, which populates `backend/.venv` from the lockfile:

```bash
uv sync --all-extras --dev --locked
```

Do **not** settle for `sudo env "PATH=$PATH" uv pip install --system
--break-system-packages -e '.[dev]'` alone. It installs the runtime deps and
leaves `backend/.venv` without the dev group — no `pytest`, no `ruff`, no
`mypy` — and that breaks more than the test command (see fact 7).

`uv run <cmd>` then resolves through the venv and is the form to use everywhere,
because it does not depend on which shell you are in or whether `PATH` was
inherited.

**Secrets.** A master key already exists in this guest. Do not generate a second
one — the secret store is envelope-encrypted, and a new key silently orphans
every stored provider credential (this has happened; the keys had to be
destroyed and re-issued).

```bash
# ~/.orchestrator-env, mode 600 — NOT ~/.bashrc (fact 2)
export ORCHESTRATOR_MASTER_KEY=...
export OPENROUTER_API_KEY=...
```

Source it from the shell that launches the server or an agent. Verify presence
without printing it:

```bash
[ -n "$ORCHESTRATOR_MASTER_KEY" ] && echo present
```

**Migrate, serve, seed.**

```bash
uv run python -m agent_orchestrator.infra.cli.main db upgrade
uv run python -m agent_orchestrator.infra.cli.main serve --port 8000   # background it
```

Check for a server already listening before starting another — a stale one
produces results that look like application bugs:

```bash
ss -ltnp | grep :8000
```

Then, with the server up:

```bash
uv run python -m agent_orchestrator.infra.cli.main seed demo   # capabilities, one agent, config keys
./seed-agents.py                                               # the six-agent free-tier roster
```

`seed demo` seeds capabilities, a single default agent, a provider and the
config keys. The six-agent OpenRouter roster this project actually runs on was
assembled by hand on top of that, so a rebuilt guest loses it — which is why
`seed-agents.py` exists. It is idempotent by *name*, reads `OPENROUTER_API_KEY`
from the environment on first run only (the key is stored envelope-encrypted
thereafter), and refuses to guess if it finds two providers named `openrouter`.
It seeds 8 capabilities, 1 provider, 4 free models (`max_inflight` 2), and 6
agents with `dev-agent` as default.

Also, not covered by cloud-init: install the `pi` CLI, authenticate `claude` and
`codex`, and regenerate the demo repo with
`bash demos/static-site-v1/scripts/materialize.sh`.

**Confirm.**

```bash
cd ~/agent-orchestrator/backend && uv run pytest -m "not integration" -q
uv run python -m agent_orchestrator.infra.cli.main config list
```

Expected config: `reasoner.mode=llm`, `agent_runner.mode=real`, and
`reasoner.provider_id` / `reasoner.model_id` pointing at the OpenRouter provider
and its Nemotron model (both UUIDs — see fact 3).

---

## Guest facts that contradict the obvious assumption

Seven things this environment does that the plan for it did not predict. Each
was found by running something on real hardware, not by reading.

**1. `uv pip install --system` fails.** Ubuntu 24.04 marks `/usr` PEP 668
externally-managed. What works is
`sudo env "PATH=$PATH" uv pip install --system --break-system-packages -e '.[dev]'`.
That deviates from `CLAUDE.md`, and is acceptable only because this guest is
disposable. A `uv venv` + `uv run` is the alternative and needs no override.

**2. `~/.bashrc` returns early for non-interactive shells.** Anything exported
there is invisible to `ssh host 'cmd'` and to an agent's tool calls. Secrets
belong in `~/.orchestrator-env` (mode 600), exported by the shell that launches
the process. A session that cannot see `ORCHESTRATOR_MASTER_KEY` will generate a
second one and silently orphan the encrypted secret store.

The same applies to `/etc/profile.d/aipom.sh`, which sets `ORCHESTRATOR_HOME`
for **login** shells only. A non-interactive remote command will not see it.

**3. Catalog IDs are server-generated UUIDs.** Only `seed demo` writes
deterministic ids like `openrouter:<model-name>`. Anything created through the
API gets a UUID. Never construct an id such as `provider:name` — always look it
up by name from the API. A model's provider binding is immutable, so a
name→id map built across *all* providers can hand out a foreign model id.

**4. `POST /api/providers` and `POST /api/agents` do not deduplicate by name.**
Re-posting creates duplicates. Two `openrouter` providers, with models parented
to one and agents bound to the other, is how `AGENT_RUNNER_CONFIG_INVALID`
(422) appears. `seed-agents.py` skips existing names and refuses outright on a
duplicate provider; clean up with `DELETE /api/providers/<id>` or
`DELETE /api/agents/<id>`.

**5. `kernel.apparmor_restrict_unprivileged_userns` is disabled**, via
`/etc/sysctl.d/60-aipom-userns.conf` shipped in cloud-init. Ubuntu 24.04 ships
it *enabled*, which stops `bwrap` and `unshare` creating an unprivileged user
namespace from a plain shell while podman and docker sail through on their own
AppArmor profiles. The symptom is a guest that runs PID-isolated containers
perfectly while several `verify.sh` checks fail with
`setting up uid map: Permission denied` — which reads exactly like a kernel wall
and is not one. **Check this sysctl before suspecting the kernel.** Disabling it
is sound *here and only here*: the VM boundary is what contains agent code, and
the host kernel is untouched.

**6. `make -C` implies `-w`, and GNU Make 4.3 does not let `-s` suppress it.**
Ubuntu 24.04 ships 4.3. A recursive `$(MAKE) -s ip` therefore emitted
`make[1]: Entering directory…` into the captured guest IP, and `ssh` tried to
resolve `make[1]:` as a hostname — while also defeating the empty-IP guard,
since make's noise is never empty. Make 4.4 made `-s` imply
`--no-print-directory`, which is why this passed review on 4.4.1. The `Makefile`
defines `VM_IP` as a plain command and never re-enters make.

---

**7. A venv without the dev group silently breaks verification, not just
testing.** The orchestrator's own verification executor shells out to commands
like `python -m pytest -q tests/…` — arbitrary strings from a task contract, run
against the repo. With `pytest` missing from `backend/.venv` those commands exit
non-zero with `No module named pytest`, which is indistinguishable from a
genuinely red check: the RED stage looks correct and the GREEN stage can never
arrive, so tasks sit at `pending` forever. Two integration tests caught it here;
a real Tier 1 run would have burned model budget on a wall no agent could climb.
`uv sync --all-extras --dev --locked` is the fix and the CI-equivalent state. It
also pins `ruff` to the locked version — a drifted local ruff reports hundreds of
findings CI never sees, which is its own way to waste an afternoon.

## Threat model

**The VM boundary — not bubblewrap — is what contains agent-written code.**

The devcontainer tried to contain agent processes *inside* the development
environment, with a seccomp profile, an AppArmor profile and bubblewrap doing
the confining. That approach failed on its own terms: the hardening needed to
make it a credible boundary is the same hardening that made nested containers
impossible, so the environment could not run the workload it existed to
develop. This guest inverts it. Confinement moved **outward**, to the
hypervisor. Inside the guest, agent code runs with ordinary privileges; the
sysctl in fact 5 is deliberately relaxed, `dev` holds NOPASSWD sudo, and both
container runtimes are fully functional. None of that weakens the boundary,
because the boundary is no longer in here.

What this means concretely:

- **The guest is cattle, not a pet.** If it is compromised, misconfigured, or
  merely confusing, the correct response is `make destroy && make up`, not
  forensics. Nothing in the guest is worth preserving for its own sake — that
  is precisely why `create-vm.sh` is idempotent and `seed-agents.py` exists as
  code rather than as a remembered sequence of curl calls. Treating the guest as
  precious would quietly re-introduce the coupling the VM was adopted to break.
- **Durable state lives in `~/.orchestrator`.** The SQLite database, the
  envelope-encrypted secret store and the config scopes are all under
  `ORCHESTRATOR_HOME` (default `~/.orchestrator`). That directory is the only
  thing worth backing up before a `destroy`, and `ORCHESTRATOR_MASTER_KEY` is
  the only thing without which its secrets are unreadable. Back up neither
  casually: the database contains encrypted provider credentials, and the key
  must never be written to a file in the repository, echoed into a log, or
  committed to `.env`.
- **The host is not in scope for agent code.** The guest reaches the host only
  through the libvirt `default` NAT network and the SSH channel you open. There
  is no shared filesystem, no bind mount of the host repository, and no docker
  socket passthrough. Work enters and leaves through git.
- **Blast radius is one `qcow2`.** Worst case, an agent gains root in the
  guest. It then owns a disposable Ubuntu image, the checked-out repository, and
  whatever credentials are in the secret store — which is the reason to scope
  the OpenRouter key to a free tier and to keep anything more valuable out of
  this guest entirely.
