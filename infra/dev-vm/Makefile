VM_NAME ?= aipom-dev
VIRSH := virsh --connect qemu:///system

# Resolve the guest address WITHOUT a recursive $(MAKE). `make -C` implies -w
# (print directory) and -s does not suppress it, so a sub-make's "Entering
# directory" text lands inside the captured value and ssh tries to resolve
# `make[1]:` as a hostname. Defining the command once keeps every caller honest.
VM_IP = $(VIRSH) domifaddr $(VM_NAME) 2>/dev/null | awk '/ipv4/ {split($$4,a,"/"); print a[1]}'

.PHONY: up ip ssh status destroy verify

up:
	./create-vm.sh

ip:
	@$(VM_IP)

ssh:
	@state="$$($(VIRSH) domstate $(VM_NAME) 2>/dev/null)"; \
	if [ "$$state" != "running" ]; then \
	  echo "ERROR: $(VM_NAME) is not running (state: $${state:-absent}). Run 'make up' first." >&2; \
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
	  echo "ERROR: $(VM_NAME) is not running (state: $${state:-absent}). Run 'make up' first." >&2; \
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
