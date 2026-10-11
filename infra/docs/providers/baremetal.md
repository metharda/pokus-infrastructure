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

A loaded driver must also support the CUDA version the pinned vLLM image is
built on. Preflight compares the CUDA version `nvidia-smi --version` reports
with the image's (major and minor) and turns `gpu_inference` off when the
driver is older, naming the update; doctor repeats the comparison
([`gpu.driver_cuda`](../how-to/fix-doctor-checks.md#check-gpu-driver_cuda)).

## Secure Boot and reboots

Pokus Infrastructure (pinka) never disables Secure Boot. A driver module built with DKMS on a host
with Secure Boot is signed with a key that must be enrolled as a Machine
Owner Key (MOK) before the module loads, and the enrollment needs someone at
the console during the next boot. So on a host with Secure Boot on and no
NVIDIA driver installed, preflight reports the action `secure_boot_mok`, and
what happens depends on `install_nvidia_driver` and on your consent:

| `install_nvidia_driver` | Consent to the MOK enrollment | Preflight | Bootstrap |
| --- | --- | --- | --- |
| `auto` | given | planned action: a warning, recorded in the host profile | installs the driver, imports the key with your one-time password and stops with exit code 76 |
| `auto` | not given | needs action | stops with exit code 30 |
| `manual` | not needed | needs action | stops with exit code 30 until you have installed the driver |
| `skip` | not needed | degraded: no `gpu_inference` | goes on without the driver |

You consent on the installer's Preflight screen, with
`--accept-mok-enrollment`, or with `gpu.accept_mok_enrollment: true` in an
answers file; the host profile and every environment fingerprint record it.
Bootstrap wants the one-time MOK password before the play starts: the
installer asks for it on its GPU screen, bootstrap on a terminal asks itself,
and a non-interactive run reads it from the first line of its standard input
(`--mok-password-stdin`). The password reaches `mokutil --import` only
through its standard input and is never written anywhere.

After installing the driver, the play looks for the module signing key
(`provider_baremetal_mok_keys`). If that key is neither enrolled nor waiting
for enrollment, the play stops with exit code 76 and names it; bootstrap
imports it and exits with 76. Reboot; at the blue MOK Manager screen choose
*Enroll MOK*, *Continue* and *Yes*, enter the one-time password and reboot
again; then run bootstrap again. It continues where it stopped and accepts
the changed host profile (the GPU is now usable), because the last run ended
with this signal. A key that already waits for enrollment only needs the
reboot.

!!! note "Not verified on hardware yet"
    Where each distribution's DKMS keeps its signing key, and how `mokutil`
    reads the password when it is not a terminal, are checked on the
    reference hosts before the first release.

If you prefer your distribution's documented procedure, install the driver
yourself, enroll its key, run preflight again and use
`install_nvidia_driver: manual`.

The same exit code is used whenever the distribution reports that a reboot is
required after an update (`/run/reboot-required`, `dnf needs-restarting -r`,
`zypper needs-rebooting`, or a kernel without matching modules on Arch).
Bootstrap never reboots by itself unless you pass `--allow-reboot`; then it
schedules the reboot one minute after it stops. On bare metal, automatic
updates leave the kernel alone unless `allow_kernel_updates` is `true`.

## The Incus storage pool

The Incus pool should live on a dedicated disk or partition
(`incus_pool_source: device`). Pokus Infrastructure formats a device only when
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
| `never_sleep` | Masks `sleep.target`, `suspend.target`, `hibernate.target` and `hybrid-sleep.target`. Switching to another policy unmasks exactly the targets Pokus Infrastructure masked. |
| `lab_session_inhibit` (default) | `pinka-sleep-inhibit.service` holds a `systemd-inhibit --what=sleep:idle --mode=block` lock while `pinka-lab.target` is up, so the machine can sleep when the lab is stopped. |
| `os_default` | Changes nothing. |

Related: [Virtual machines](vm.md), [WSL2 hosts](wsl2.md),
[How to troubleshoot a bootstrap run](../how-to/troubleshoot.md#exit-code-76).
