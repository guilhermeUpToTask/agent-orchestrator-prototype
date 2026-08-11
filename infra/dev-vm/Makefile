VM_NAME ?= praxis-dev
VIRSH := virsh --connect qemu:///system

# Resolve the guest address WITHOUT a recursive $(MAKE). `make -C` implies -w
# (print directory) and -s does not suppress it, so a sub-make's "Entering
# directory" text lands inside the captured value and ssh tries to resolve
# `make[1]:` as a hostname. Defining the command once keeps every caller honest.
VM_IP = $(VIRSH) domifaddr $(VM_NAME) 2>/dev/null | awk '/ipv4/ {split($$4,a,"/"); print a[1]}'

# A domain that exists but is powered off needs `start`, NOT `up` — create-vm.sh
# is idempotent and exits 0 with "already exists" without booting anything. The
# guest lands here routinely: virt-install's --cloud-init sets on_reboot=destroy
# for the seed boot, so the first in-guest `reboot` shuts the domain off instead
# of restarting it.
GUEST_DOWN_HINT = "Run 'make start' (or 'make up' if it was never created)."

.PHONY: up start ip ssh status destroy verify

up:
	./create-vm.sh

start:
	@if [ "$$($(VIRSH) domstate $(VM_NAME) 2>/dev/null)" = "running" ]; then \
	  echo "$(VM_NAME) is already running."; \
	else \
	  $(VIRSH) start $(VM_NAME); \
	fi

ip:
	@$(VM_IP)

ssh:
	@state="$$($(VIRSH) domstate $(VM_NAME) 2>/dev/null)"; \
	if [ "$$state" != "running" ]; then \
	  echo "ERROR: $(VM_NAME) is not running (state: $${state:-absent}). "$(GUEST_DOWN_HINT) >&2; \
	  exit 1; \
	fi; \
	vmip="$$($(VM_IP))"; \
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
	  echo "ERROR: $(VM_NAME) is not running (state: $${state:-absent}). "$(GUEST_DOWN_HINT) >&2; \
	  exit 1; \
	fi; \
	vmip="$$($(VM_IP))"; \
	if [ -z "$$vmip" ]; then \
	  echo "ERROR: $(VM_NAME) is running but has no DHCP lease yet. Wait a few seconds and retry." >&2; \
	  exit 1; \
	fi; \
	scp -o StrictHostKeyChecking=accept-new verify.sh dev@$$vmip:/tmp/verify.sh; \
	ssh dev@$$vmip 'bash /tmp/verify.sh'

destroy:
	-$(VIRSH) destroy $(VM_NAME)
	$(VIRSH) undefine $(VM_NAME) --remove-all-storage
