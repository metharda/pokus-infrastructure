---
title: Virtual machine hosts
type: explanation
audience: [operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Running a lab host in a VM - guest agents, GPU passthrough, nested virtualization and measurement caveats.
---

# Virtual machine hosts

A lab host may itself be a virtual machine. This is also the way out for
hosts Pokus cannot support directly (immutable or non-systemd
distributions, unsupported architectures): run a supported distribution in a
VM on that host. The `provider_vm` role applies when preflight detects a
hypervisor.

## Guest agent

The provider installs and enables the hypervisor's guest agent so that the
hypervisor can shut the VM down cleanly and report its state:

| Hypervisor | Debian family | Red Hat family |
| --- | --- | --- |
| KVM, QEMU | `qemu-guest-agent` | `qemu-guest-agent` |
| VMware | `open-vm-tools` | `open-vm-tools` |
| Microsoft Hyper-V | `linux-cloud-tools-virtual` (Ubuntu), `hyperv-daemons` (Debian) | `hyperv-daemons` |

The package names for the SUSE and Arch families, and for the Red Hat family,
are still being verified; the role lists them in
`ansible/roles/provider_vm/vars/<family>.yml`.

## GPU passthrough

A GPU is used only if the hypervisor passes a physical GPU through to the VM
(PCI passthrough, also called VFIO or DDA). Preflight then sees a real NVIDIA
device, and the provider installs the driver exactly as on
[bare metal](baremetal.md#nvidia-driver), including the Secure Boot flow
inside the VM. Virtual GPUs and shared GPU modes are not supported. What
passthrough needs differs per hypervisor:

| Hypervisor | Requirements |
| --- | --- |
| KVM/QEMU (libvirt, Proxmox) | IOMMU enabled in firmware and on the host kernel command line; the GPU in its own IOMMU group, bound to `vfio-pci` on the host |
| VMware ESXi | the GPU enabled for passthrough on the host, VM memory fully reserved |
| Hyper-V | Discrete Device Assignment on Windows Server; not available on client Windows (use [WSL2](wsl2.md) there) |

## Nested virtualization

VM targets need `/dev/kvm` inside the lab VM, so the hypervisor must expose
virtualization extensions to the guest (for example `host-passthrough` CPU
mode on KVM, "Expose hardware assisted virtualization" on VMware). Without
them preflight reports `vm_targets` as disabled and the lab runs container
targets only.

## Measurement caveats

Latency and resource measurements in a VM include the hypervisor's overhead
and can be disturbed by other VMs on the same host. The hypervisor is
recorded in the environment fingerprint, and reports keep results from VMs
apart from bare-metal results. Power and reboots work as on bare metal; the
power policy only affects the guest.

Related: [Portability](../explanation/portability.md),
[ADR-0003 (provider model)](../adr/0003-provider-model.md).
