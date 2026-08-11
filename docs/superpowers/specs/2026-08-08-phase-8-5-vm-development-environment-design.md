# Phase 8 / P8.5 — a VM development environment, so containers are real

**Date:** 2026-08-08
**Status:** accepted, not yet implemented
**Branch:** `phase-8-5-container-environment`
**Supersedes:** `.devcontainer/` and `.devcontainer/BUBBLEWRAP.md` as the
development environment of record.

## The problem, established empirically

P8.5 — the container adapter behind `ProjectEnvironment` — has been parked since
2026-08-02 because "the development environment cannot run containers". That was
true, but the recorded diagnosis was wrong in a way that mattered, so it was
re-tested from scratch on 2026-08-08 inside the running devcontainer.

| Probe | Result |
|---|---|
| `docker` binary, `/var/run/docker.sock` | absent — no Docker-outside-of-Docker |
| `CapEff` | `0xa80425fb`, the stock Docker set — **no `CAP_SYS_ADMIN`** |
| podman 5.x, buildah, crun | installed; `vfs` + `ignore_chown_errors` configured |
| image pull, storage, user namespaces | **all work** |
| `podman run` | dies at `` mount `proc` to `proc`: Operation not permitted `` |
| `bwrap --proc /proc` | fails identically — the **kernel**, not podman |
| `/sys/fs/cgroup` | read-only; cgroup2 **cannot** be mounted in a userns either |
| hand-rolled OCI bundle via `crun` | **a real container ran** |

### The correction

The roadmap names masked `/proc` the final blocker. It is real, but it is not
one wall — it is **two walls that deadlock each other**:

1. **13 masked `/proc` submounts** trip the kernel's `mount_too_revealing`
   check, which forbids mounting a fresh `procfs`, which forbids a **private PID
   namespace**.
2. **`/sys/fs/cgroup` is read-only**, so podman requires `--cgroups=disabled` —
   but that flag *forces* a private PID namespace, which trips wall 1.

Every workaround for one triggers the other. That is why it read as final.

But a fresh `procfs` is only needed *because* of PID isolation. Bind the
existing `/proc`, share the host PID namespace, skip cgroups, and a container
boots in this devcontainer today — proven with a hand-written OCI bundle. The
accurate statement is therefore:

> This environment can run containers. It cannot run **PID-isolated** ones.

That distinction is the entire design space, and it is why no amount of
devcontainer tuning resolves the problem.

## Why a VM, and not a better container

The development environment must be two contradictory things at once:

- **privileged enough** to nest containers — full `CAP_SYS_ADMIN`, unmasked
  `/proc`, writable cgroups;
- **isolated enough** to contain agent-written code — the threat model
  `BUBBLEWRAP.md` was written for.

A container cannot be both, and the masked-`/proc` wall *is* the kernel enforcing
exactly that. A VM is both by construction: a full guest kernel grants every
privilege inside it, and the hardware boundary provides containment outside it.

The VM is therefore not a workaround for the devcontainer's limits. It is the
correct shape for the requirement, and it dissolves both walls rather than
routing around either.

### Alternatives rejected

- **Unmask `/proc` in the devcontainer** (`--security-opt
  systempaths=unconfined`). One flag, and it very likely works. Rejected because
  it exposes `/proc/kcore` — host kernel memory — to everything in the
  container, permanently thinning the one boundary between agent code and the
  maintainer's machine, in exchange for a capability a VM provides outright.
- **Privileged podman/DinD sidecar.** Keeps the devcontainer hardened but moves
  privilege to a sibling that runs agent-produced code, which is a shorter path
  to host root than the option above.
- **Docker-outside-of-Docker via a mounted socket.** Equivalent to handing out
  host root; forbidden by `BUBBLEWRAP.md` for that reason.
- **Scripted fake container CLI as the primary validation.** Rejected by the
  maintainer: it loses precisely the behaviour P8.5 exists to prove. It survives
  only in the narrow role described under *Testing*.

## Architecture

```
Host: Ubuntu 24.04, bare metal, Intel i3-10100F (VT-x present), 15 GiB RAM
└── libvirt/KVM guest "praxis-dev" — Ubuntu 24.04 cloud image
    ├── full guest kernel: CAP_SYS_ADMIN, unmasked /proc, writable cgroup2
    ├── repo + ~/.orchestrator on native ext4
    ├── podman AND docker
    ├── bubblewrap — works unimpeded, including --proc /proc
    └── P8.5 acceptance-run containers, fully PID-isolated
```

Access is over SSH, which keeps VS Code available via Remote-SSH; abandoning the
editor was considered and is an unrelated sacrifice, since the devcontainer
mechanism is what constrains the work, not the IDE. libvirt's default NAT network
gives the guest a `192.168.122.x` address the host browser reaches directly, so
the console on `:8000` and Vite on `:5173` need no port forwarding.

The guest is **cattle, not a pet**: rebuildable from the checked-in spec at any
time, with all durable state under `~/.orchestrator`.

### Both runtimes, deliberately

The roadmap already decided the container binary is configuration rather than a
hardcoded `docker`. Installing both lets P8.5's tests *prove* that decision
against two real CLIs instead of asserting it.

## Repository layout

`infra/dev-vm/` replaces `.devcontainer/`:

| File | Purpose |
|---|---|
| `cloud-init/user-data.yaml` | packages, user, SSH keys, Python + uv + Node, podman + docker + bubblewrap, and the `pi` / `codex` / `claude` CLIs |
| `create-vm.sh` | idempotent `virt-install` wrapper |
| `Makefile` | `up` / `ssh` / `destroy` / `status` / `verify` |
| `verify.sh` | the capability proof (below) |
| `README.md` | setup and threat model — successor to `BUBBLEWRAP.md` |

## VM specification

4 vCPU, 8 GiB RAM, **40 GiB sparse qcow2**.

**Disk is the binding constraint, not CPU or RAM.** The host has 49 GiB free of
218 GiB. A host image prune is a plan step with a measured before/after, not an
assumption; if it does not free meaningful space, the VM disk shrinks rather
than the host filling up.

## Bootstrap — no migration

The maintainer has elected to **discard existing environment state and rotate
credentials**, which removes the riskiest part of this work. There is no rsync
of `~/.orchestrator`, no carrying of `ORCHESTRATOR_MASTER_KEY`, and no rescue of
the `codex-state` / `claude-state` Docker volumes.

First-run bootstrap in the guest:

1. Generate a **new** `ORCHESTRATOR_MASTER_KEY`.
2. Re-enter the OpenRouter API key; re-authenticate the agent CLIs.
3. `orchestrate db upgrade`, then `orchestrate seed demo`.
4. Re-point the reasoner and the six agents at their free OpenRouter models —
   `seed demo` overwrites this configuration, so it is a bootstrap step in its
   own right rather than something the seed leaves correct.
5. Regenerate the P8.4 demo repository with
   `demos/static-site-v1/scripts/materialize.sh`.

**What is lost is small and recoverable.** The P8.4 demo project (`e0e54bc8`)
and its seeded repository regenerate from a script checked into this repository.
The roadmap's "staged and verified" P8.4 state is genuinely gone and must be
re-staged — accepted knowingly, since the key rotation was wanted anyway.

## Verification — proving the walls are gone

`verify.sh` asserts exactly what fails in the devcontainer today, so the
roadmap's blocker table is replaced by evidence rather than by a claim:

1. `bwrap --ro-bind / / --unshare-user --unshare-pid --proc /proc` succeeds.
2. A fresh `procfs` mounts inside a private PID namespace.
3. `/sys/fs/cgroup` is writable and cgroup2 mounts in a user namespace.
4. `podman run` succeeds **without** `--cgroups=disabled`, with a private PID
   namespace.
5. `docker run` succeeds, with a private PID namespace.
6. Rootless podman succeeds with full isolation.

Each check prints the assertion and its result. The script is the artifact this
design is judged by; a green run is what promotes P8.5 from parked to workable.

## What this unblocks: P8.5

With the environment capable, the adapter is ordinary work:

- `infra/environment/container_environment.py` implementing `ProjectEnvironment`
  — the container binary read from configuration, never hardcoded.
- Integration tests marked `integration`, running **real containers**: boot,
  healthcheck, scenario, teardown, timeout, teardown-on-failure.
- `verify()` must not raise; the not-installed and daemon-down paths return
  `errored` with an actionable message.

### Testing note

Two unhappy paths — "binary absent" and "daemon dies mid-run" — cannot be staged
against a live daemon on demand. Those specific cases use a scripted CLI as
**failure injection**, the pattern `test_runner_taxonomy.py` already uses. This
is not a stand-in for real containers; every path that can be exercised for real
is exercised for real.

## Documentation consequences

Per the repository's docs discipline, these land in the same change:

- `ROADMAP.md` — correct *Containerization is unavailable* to the
  two-deadlocked-walls finding, and record that the VM resolves both. Move P8.5
  out of parked.
- `docs/decisions/decision-log.md` — an entry for the environment move. **No
  domain un-freeze**: nothing here touches the domain.
- `.devcontainer/` — removed. No CI workflow references it (`ci.yml`,
  `development-workflow.yml`, `pr-title.yml`, `release-please.yml` all
  verified), so removal is safe.
- `CLAUDE.md` — build and run instructions updated for the guest.

Retiring the AppArmor and seccomp pair costs the product nothing: `grep` finds
**no bubblewrap usage anywhere in `backend/`**. `BUBBLEWRAP.md` governs the
Codex and Pi CLIs sandboxing themselves, not the orchestrator's `Sandbox` port.

## Risks

| Risk | Mitigation |
|---|---|
| Host disk exhaustion | Prune first, measure, size the qcow2 to what is actually free |
| VM setup consumes the session | `verify.sh` is the gate; P8.5 does not start until it is green |
| Nested virtualization unavailable | Not applicable — bare metal with `vmx` confirmed |
| Guest drifts from the checked-in spec | Cattle, not a pet: rebuild rather than repair |

## Exit criteria

1. `infra/dev-vm/verify.sh` passes all six checks in the guest.
2. The full backend suite, `ruff` and `mypy` pass in the guest.
3. A `ContainerEnvironment` acceptance run boots a real container against a real
   repository and records a real verdict, under both podman and docker.
4. `.devcontainer/` is gone and the roadmap tells the truth about why.
