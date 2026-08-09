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
