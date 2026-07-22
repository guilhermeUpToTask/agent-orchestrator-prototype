# Bubblewrap in the devcontainer

This setup allows Codex and Pi to create unprivileged Bubblewrap sandboxes
inside the devcontainer without granting the outer container `SYS_ADMIN`,
running it as `--privileged`, mounting the Docker socket, or disabling
AppArmor globally.

## Threat model

The devcontainer still has writable access to the repository and to the host's
`~/.orchestrator` directory. Bubblewrap only protects processes that are
actually launched through a restrictive Bubblewrap policy. Do not expose
`.env`, `/root/.codex`, `/root/.claude`, or unrelated workspaces to untrusted
worker processes.

The checked-in seccomp profile is an allow-by-default profile with explicit
denials for host-dangerous syscall families. It is intentionally more
permissive than Docker's default profile because nested unprivileged namespace
creation requires `clone`, `unshare`, mount, and related calls. Do not combine
this setup with `SYS_ADMIN`, `--privileged`, `seccomp=unconfined`, or a mounted
Docker socket.

## Host prerequisites

These steps target an Ubuntu host with AppArmor enabled. Run them in a **host
terminal**, not inside the devcontainer.

1. Confirm that unprivileged user namespaces exist and AppArmor mediation is
   enabled:

   ```bash
   cat /proc/sys/kernel/unprivileged_userns_clone
   cat /proc/sys/user/max_user_namespaces
   cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns
   ```

   The first value should be `1`, and `max_user_namespaces` should be greater
   than zero. On Ubuntu 24.04+, the AppArmor restriction is normally `1`; keep
   it enabled.

2. Install the repository's dedicated AppArmor profile:

   ```bash
   sudo install -m 0644 \
     .devcontainer/apparmor-agent-orchestrator-bwrap \
     /etc/apparmor.d/agent-orchestrator-bwrap
   sudo apparmor_parser -r /etc/apparmor.d/agent-orchestrator-bwrap
   sudo apparmor_status | grep agent-orchestrator-bwrap
   ```

   The final command should print `agent-orchestrator-bwrap`.

3. Confirm `.devcontainer/devcontainer.json` uses both checked-in policies:

   ```jsonc
   "--security-opt",
   "seccomp=${localWorkspaceFolder}/.devcontainer/seccomp-bwrap.json",
   "--security-opt",
   "apparmor=agent-orchestrator-bwrap"
   ```

4. In VS Code, run **Dev Containers: Rebuild Container**. Restarting only the
   shell is insufficient because Docker applies security options at container
   creation.

## Verification inside the rebuilt container

1. Confirm Bubblewrap can create an unprivileged user namespace:

   ```bash
   bwrap --ro-bind / / --unshare-user \
     /bin/sh -c 'echo USERNS_OK; id'
   ```

   Expected output includes:

   ```text
   USERNS_OK
   uid=0(root) gid=0(root) groups=0(root)
   ```

   This `root` identity exists only inside the child user namespace.

2. Confirm the outer container does not have `SYS_ADMIN`:

   ```bash
   python3 - <<'PY'
   status = open('/proc/self/status', encoding='utf-8').read().splitlines()
   cap_eff = int(next(line.split()[1] for line in status if line.startswith('CapEff:')), 16)
   print('SYS_ADMIN present:', bool(cap_eff & (1 << 21)))
   PY
   ```

   Expected output:

   ```text
   SYS_ADMIN present: False
   ```

3. Confirm the Docker socket is absent:

   ```bash
   test ! -e /var/run/docker.sock && echo 'Docker socket absent'
   ```

4. Confirm both installed agent CLIs start in a direct Bubblewrap sandbox:

   ```bash
   bwrap \
     --ro-bind / / \
     --unshare-user \
     --unshare-pid \
     --unshare-ipc \
     --unshare-uts \
     --dev /dev \
     --die-with-parent \
     --new-session \
     /bin/sh -c 'codex --version; pi --version; echo AGENTS_OK'
   ```

   `codex`, `pi`, and `AGENTS_OK` should all appear.

## Docker procfs limitation

Do not add `--proc /proc` to nested Bubblewrap invocations in this
devcontainer. Docker masks sensitive paths in the outer container's procfs,
and the kernel rejects a fresh nested procfs mount with:

```text
VFS: Mount too revealing
```

This is a kernel safety check. Do not bypass it with `SYS_ADMIN`,
`--privileged`, or `systempaths=unconfined`. The tested agent startup command
inherits Docker's restricted procfs. A stronger worker policy should avoid
exposing coordinator processes and credentials, or run workers in disposable
outer containers/VMs when a private procfs is required.

## Writable worktree and networking

`--ro-bind / /` makes the filesystem read-only. A coding worker needs an
explicit narrow writable mount, preferably a disposable worktree:

```bash
bwrap \
  --ro-bind / / \
  --bind /path/to/disposable-worktree /workspace \
  --chdir /workspace \
  --unshare-user \
  --unshare-pid \
  --unshare-ipc \
  --unshare-uts \
  --dev /dev \
  --die-with-parent \
  --new-session \
  pi --version
```

Add `--unshare-net` only for processes that do not need a remote model API.
Codex and Pi require network access for normal remote-provider requests unless
traffic is routed through a separately designed broker or allowlisted proxy.

## Troubleshooting

- `setting up uid map: Permission denied`: confirm the host AppArmor profile is
  loaded and `devcontainer.json` selects it. Check host denials with
  `sudo journalctl -k --since '5 minutes ago' | grep -Ei 'apparmor|denied|bwrap'`.
- `Can't mount proc ... Operation not permitted` plus `VFS: Mount too
  revealing`: remove `--proc /proc`; do not broaden outer-container privilege.
- `Creating new namespace failed`: confirm the custom seccomp profile is active
  and rebuild the container.
- A Bubblewrap test launched from an already sandboxed Codex command can fail
  because it attempts nested sandboxing. Run direct Bubblewrap validation from
  the outer devcontainer shell.

## Rollback

Remove the two Bubblewrap `--security-opt` entries from
`.devcontainer/devcontainer.json` and rebuild the devcontainer. To remove the
host profile:

```bash
sudo apparmor_parser -R /etc/apparmor.d/agent-orchestrator-bwrap
sudo rm /etc/apparmor.d/agent-orchestrator-bwrap
```
