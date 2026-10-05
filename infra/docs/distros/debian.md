---
title: Debian family (Debian, Ubuntu and derivatives)
type: explanation
audience: [operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Sources, holds, updates, firewall, MAC and NVIDIA notes for Debian, Ubuntu and their derivatives.
---

# Debian family

The `debian` family covers Debian, Ubuntu and their derivatives (Linux Mint,
Pop!_OS, Zorin OS, elementary OS, Kali, ...). Ubuntu 24.04 LTS and Debian 13
are the **supported** releases; which other releases are accepted is in the
[support matrix](../support-matrix.md).

## Derivatives

A derivative is provisioned as the Ubuntu or Debian release it is based on.
Preflight reads the parent's codename from `UBUNTU_CODENAME` or
`DEBIAN_CODENAME` in `/etc/os-release`, and repository URLs and version pins
use that codename: Linux Mint 22 gets the packages for Ubuntu 24.04 (`noble`).
Derivatives are in the experimental tier and need `--accept-experimental`.

## Package sources and pins

| Component | Source |
| --- | --- |
| Docker Engine, Compose, buildx | Docker's apt repository `download.docker.com/linux/<distro>` |
| NVIDIA Container Toolkit | NVIDIA's apt repository |
| Incus | Zabbly's Incus 6.0 LTS repository (being verified) |
| Tailscale | Tailscale's apt repository (being verified) |
| NVIDIA driver | the distribution: `nvidia-driver-580-open` on Ubuntu, `nvidia-driver` from `non-free` on Debian |

`<distro>` is `ubuntu` or `debian`. Repository keys are installed as
`signed-by` keyrings under `/etc/apt/keyrings/`, never with `apt-key`. The
pinned versions per release are in [Pinned versions](../reference/versions.md);
the full matrix is in [Component sources](../reference/component-sources.md).

## Holds and updates

- **Holds:** apt preferences in `/etc/apt/preferences.d/pokus-pins` pin every
  Pokus-managed package to its locked version with priority 1001.
- **Automatic updates:** `unattended-upgrades` with the security origin only
  (`<codename>-security`), configured in
  `/etc/apt/apt.conf.d/52pokus-unattended-upgrades`. It never reboots and
  never touches Docker, containerd, NVIDIA, Incus or Tailscale packages, nor
  the kernel on bare metal unless `allow_kernel_updates` is `true`.
- Other updates go through `pokus-lab update-host`.

## Firewall and MAC

- **Firewall:** ufw if it is active (common on Ubuntu desktops), otherwise
  Pokus's own nftables table. firewalld is used if you installed and enabled
  it. See [How to configure the firewall backend and SELinux](../how-to/firewall-and-selinux.md).
- **MAC:** AppArmor. Docker and Incus ship their own profiles; Pokus disables
  nothing.

## NVIDIA driver

`install_nvidia_driver: auto` is **supported** on Ubuntu (the pinned
`nvidia-driver-580-open` package) and Debian (`nvidia-driver`, whose exact
setup is still being verified). Both build the kernel module with DKMS; with
Secure Boot the module's key is enrolled through the MOK flow described in
[Bare-metal hosts](../providers/baremetal.md#secure-boot-and-reboots). The
initramfs is rebuilt with `update-initramfs`.

## Known issues

- The SSH service is called `ssh` on this family (`sshd` elsewhere); Pokus
  handles this, but keep it in mind when you check the service yourself.
- Ubuntu 26.04 LTS is listed in the matrix as soon as Docker, Incus, the
  NVIDIA Container Toolkit and Tailscale publish packages for it.
