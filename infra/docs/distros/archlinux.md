---
title: Arch family (Arch Linux and derivatives)
type: explanation
audience: [operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Sources, holds, updates, firewall and NVIDIA notes for Arch Linux and its derivatives.
---

# Arch family

The `archlinux` family covers Arch Linux (`tested`) and derivatives such as
Manjaro and EndeavourOS (experimental). All of them are rolling releases.
The [support matrix](../support-matrix.md) has the details.

## Package sources

Every component comes from the official Arch repositories with the
distribution's own keyring: Docker, Incus, Tailscale, the NVIDIA Container
Toolkit and the NVIDIA driver (`nvidia-open`). The AUR is never used. Some
package names are still being verified; see
[Component sources](../reference/component-sources.md).

## Holds and updates

- **Holds:** packages are installed at their current version and added to
  `IgnorePkg` in a managed block of `/etc/pacman.conf`, so `pacman -Syu`
  leaves them alone. Their versions are recorded in the fingerprint.
- **Updates:** there are no automatic updates. Update the host with
  `pinka update-host`, which drains the lab, updates, re-runs bootstrap
  and doctor, and shows the fingerprint difference. Results are comparable
  only within one fingerprint.

## Firewall and MAC

- **Firewall:** Arch enables no firewall manager by default, so Pokus Infrastructure (pinka) uses
  its own nftables table (`pinka-nftables.service`). If you enabled firewalld
  or ufw, Pokus Infrastructure uses that instead.
- **MAC:** none by default. Hosts that added SELinux or AppArmor manage it
  themselves.

## NVIDIA driver

`install_nvidia_driver: auto` installs `nvidia-open` (prebuilt for the
distribution kernel) and rebuilds the initramfs with `mkinitcpio`. With
Secure Boot, Arch has no MOK tooling in the default installation and Pokus Infrastructure
signs nothing: sign the kernel and modules yourself (for example with
`sbctl`), or use `install_nvidia_driver: manual`.

## Known issues

- After a kernel update the running kernel has no matching modules until the
  next boot; bootstrap detects this and stops with exit code 76 (reboot).
- Derivatives may lag behind Arch's repositories; their tier stays
  experimental.
