# provider_vm

VM provider.

- Installs and enables the hypervisor's guest agent (`vars/<family>.yml`):
  `qemu-guest-agent` (KVM, QEMU), `open-vm-tools` (VMware), the Hyper-V
  daemons (Microsoft).
- NVIDIA driver, power policy and reboot handling as on bare metal, but the
  driver only with GPU passthrough (`host_profile.gpu.path == passthrough`).
- Notes that latency and resource measurements include virtualization
  overhead; the hypervisor is recorded in the fingerprint.

## Variables

See `meta/argument_specs.yml`.

## Feature flags

`gpu_inference`, `vm_targets` (nested virtualization).

## Idempotency

As provider_baremetal; the guest agent is a package and a service.
