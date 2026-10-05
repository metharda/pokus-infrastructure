---
title: Bare-metal hosts
type: explanation
audience: [operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: NVIDIA driver installation with Secure Boot, reboots, the Incus pool disk and power policies on physical hosts.
---

# Bare-metal hosts

On a physical machine the `provider_baremetal` role installs the NVIDIA
driver if needed, handles reboots and Secure Boot key enrollment, and applies
the power policy. The settings are in host_vars; every variable is listed in
[Ansible variables](../reference/ansible-variables.md#provider_baremetal).

## NVIDIA driver

`install_nvidia_driver` decides what happens when preflight finds an NVIDIA
GPU without a working driver:

| Value | Behavior |
| --- | --- |
| `auto` | Install the driver with the family's method, pinned and held. |
| `manual` | Stop with exit code 77; you install the driver as your distribution documents it, and preflight must then find it loaded. |
| `skip` | Do nothing; the lab runs without local inference. |

The `auto` methods come from the
[component source matrix](../reference/component-sources.md#nvidia_driver).
Only Ubuntu and Debian are `supported`; the other families are `tested` or
not verified yet, and bootstrap stops with exit code 77 instead of guessing
where no verified method exists. Fedora's driver comes from RPM Fusion, a
community repository: it needs `--allow-community-repos`, which is recorded in
the fingerprint. Before installing, the role blacklists the `nouveau` driver
and rebuilds the initramfs with the family's tool (`update-initramfs`,
`dracut` or `mkinitcpio`). The driver packages are held, so automatic updates
never replace them.

## Secure Boot and reboots

Pokus never disables Secure Boot. When the driver's kernel module was built
with DKMS on a host with Secure Boot, its signing key must be enrolled as a
Machine Owner Key (MOK). Bootstrap then stops with exit code 76. Reboot; at
the blue MOK Manager screen choose to enroll the key and enter the password
set during installation; then run bootstrap again.

The same exit code is used whenever the distribution reports that a reboot is
required after an update (`/run/reboot-required`, `dnf needs-restarting -r`,
`zypper needs-rebooting`, or a kernel without matching modules on Arch).
Bootstrap never reboots by itself unless you pass `--allow-reboot`; then it
schedules the reboot one minute after it stops. On bare metal, automatic
updates leave the kernel alone unless `allow_kernel_updates` is `true`.

## The Incus storage pool

The Incus pool should live on a dedicated disk or partition
(`incus_pool_source: device`). Pokus formats a device only when
`incus_pool_device_confirm` repeats `incus_pool_device` exactly, the device is
not mounted and carries no filesystem signature (unless
`incus_pool_device_force_wipe` is set). A loop-backed pool inside the root
filesystem is the fallback.

!!! warning "Data loss"
    Formatting a device destroys everything on it. Use a path under
    `/dev/disk/by-id/` so the name cannot point to a different disk after a
    reboot, and check it twice.

## Power policy

`power_policy` controls whether the host may sleep:

| Value | Behavior |
| --- | --- |
| `never_sleep` | Masks `sleep.target`, `suspend.target`, `hibernate.target` and `hybrid-sleep.target`. Switching to another policy unmasks exactly the targets Pokus masked. |
| `lab_session_inhibit` (default) | `pokus-sleep-inhibit.service` holds a `systemd-inhibit --what=sleep:idle --mode=block` lock while `pokus-lab.target` is up, so the machine can sleep when the lab is stopped. |
| `os_default` | Changes nothing. |

Related: [Virtual machines](vm.md), [WSL2 hosts](wsl2.md),
[How to troubleshoot a bootstrap run](../how-to/troubleshoot.md#exit-code-76).
