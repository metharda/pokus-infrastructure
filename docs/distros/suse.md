---
title: SUSE family (openSUSE and SLES)
type: explanation
audience: [operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Sources, holds, updates, firewall, MAC and NVIDIA notes for openSUSE Leap, openSUSE Tumbleweed and SLES.
---

# SUSE family

The `suse` family covers openSUSE Leap 15.6 and 16.0 and openSUSE Tumbleweed
(`tested`), and SLES (experimental). Tumbleweed is a rolling release. The
[support matrix](../support-matrix.md) has the details.

## Package sources and pins

| Component | Source |
| --- | --- |
| Docker Engine, Compose, buildx | the distribution (package names being verified) |
| NVIDIA Container Toolkit | NVIDIA's rpm repository, added with zypper |
| Incus | the distribution or OBS (being verified) |
| Tailscale | Tailscale's rpm repository (being verified) |
| NVIDIA driver | NVIDIA's repository with the open kernel module packages (being verified) |

Repository keys are imported explicitly with `rpm --import` after their
fingerprint has been checked, before the repository is added; Pokus Infrastructure (pinka) never
uses `zypper --gpg-auto-import-keys`. Leap releases are pinned under
`opensuse-leap-<version>` in `versions.lock.yml`; Tumbleweed is not pinned
(see below). Details: [Component sources](../reference/component-sources.md).

## Holds and updates

- **Holds:** `zypper addlock` for every pinka-managed package.
- **Automatic updates (Leap):** `pinka-security-patch.timer` runs
  `zypper --non-interactive patch --category security` daily without a
  reboot.
- **Tumbleweed:** packages are installed at their current version, held, and
  their versions recorded in the fingerprint. There are no automatic updates;
  update with `pinka update-host`, which keeps the holds and shows the
  fingerprint difference. Results are comparable only within one fingerprint.

## Firewall and MAC

- **Firewall:** firewalld is active by default on openSUSE; Pokus Infrastructure adds the
  `pinka-tailnet` zone as on the [Red Hat family](redhat.md#firewall-and-selinux).
- **MAC:** AppArmor on Leap 15.6; newer installations may use SELinux.
  Preflight records which one is active; with SELinux the `mac` role labels
  the research space as on the Red Hat family (its package names on openSUSE
  are being verified).

## NVIDIA driver

`install_nvidia_driver: auto` is `tested` on this family and uses NVIDIA's
repository for openSUSE with the open kernel module packages once they are
verified; until then bootstrap stops with exit code 77 and asks you to
install the driver yourself. The initramfs is rebuilt with `dracut`; Secure
Boot uses the MOK flow.

## Known issues

- Several package names on this family have not been verified yet; the play
  refuses unverified entries rather than guessing.
