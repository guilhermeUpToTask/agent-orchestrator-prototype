VM_NAME ?= aipom-dev
VIRSH := virsh --connect qemu:///system

.PHONY: up ip ssh status destroy verify

up:
	./create-vm.sh

ip:
	@$(VIRSH) domifaddr $(VM_NAME) | awk '/ipv4/ {split($$4,a,"/"); print a[1]}'

ssh:
	@ssh -o StrictHostKeyChecking=accept-new dev@$$($(MAKE) -s ip)

status:
	@$(VIRSH) dominfo $(VM_NAME)

verify:
	@scp -o StrictHostKeyChecking=accept-new verify.sh dev@$$($(MAKE) -s ip):/tmp/verify.sh
	@ssh dev@$$($(MAKE) -s ip) 'bash /tmp/verify.sh'

destroy:
	-$(VIRSH) destroy $(VM_NAME)
	$(VIRSH) undefine $(VM_NAME) --remove-all-storage
