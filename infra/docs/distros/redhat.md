---
title: Red Hat family (Fedora, RHEL and compatibles)
type: explanation
audience: [operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Sources, holds, updates, firewall, SELinux and NVIDIA notes for Fedora, RHEL, AlmaLinux, Rocky Linux, CentOS Stream and Oracle Linux.
---

# Red Hat family

The `redhat` family covers Fedora (the current and the previous release),
RHEL, AlmaLinux, Rocky Linux and CentOS Stream 9 and 10 (all `tested`), and
Oracle Linux (experimental). RHEL-compatible major 8 is unsupported: it boots
with cgroup v1 by default. The [support matrix](../support-matrix.md) has the
details.

## Package sources and pins

| Component | Source |
| --- | --- |
| Docker Engine, Compose, buildx | Docker's rpm repositories: `linux/fedora` on Fedora, `linux/centos` on RHEL-compatible releases |
| NVIDIA Container Toolkit | NVIDIA's rpm repository |
| Incus | the distribution (Fedora) or EPEL/COPR (RHEL-compatible; being verified) |
| Tailscale | Tailscale's rpm repository (being verified) |
| NVIDIA driver | RPM Fusion `akmod-nvidia` on Fedora (community repository); NVIDIA's repository on RHEL-compatible releases (being verified) |

All RHEL-compatible releases share the release keys `el-9` and `el-10` in
`versions.lock.yml`. Repository keys are imported with `rpm --import` after
their fingerprint has been checked, and every repository has `gpgcheck=1`.
Details: [Component sources](../reference/component-sources.md).

## Holds and updates

- **Holds:** `dnf versionlock` (the plugin is installed by the `pkg` role).
- **Automatic updates:** `dnf-automatic` with `upgrade_type = security`,
  `reboot = never`, and `exclude = kernel*` on bare metal unless
  `allow_kernel_updates` is `true`. Fedora is a fast-moving release model:
  move to a new Fedora release with `pokus-lab update-host` and expect the
  fingerprint to change.

## Firewall and SELinux

- **Firewall:** firewalld, which is active by default on this family. Pokus
  adds the zone `pokus-tailnet` for `tailscale0` and puts the image build
  bridge into the `trusted` zone; Docker's own `docker` zone is left alone.
- **SELinux stays enforcing.** The `mac` role installs
  `policycoreutils-python-utils` and `container-selinux`, adds persistent file
  context rules for the research space, and runs `restorecon` when they
  change. See [How to configure the firewall backend and SELinux](../how-to/firewall-and-selinux.md).

## NVIDIA driver

`install_nvidia_driver: auto` is `tested`, not `supported`, on this family. On
Fedora it uses RPM Fusion's `akmod-nvidia`, which needs
`--allow-community-repos`; akmods signs the module with its own key, which is
enrolled with the MOK flow. On RHEL-compatible releases NVIDIA's own
repository is used once its current recommendation has been verified; until
then bootstrap stops with exit code 77 and asks you to install the driver
yourself (`install_nvidia_driver: manual`). The initramfs is rebuilt with
`dracut`.

## Known issues

- NVIDIA's own repository file for the container toolkit sets `gpgcheck=0`.
  Pokus configures the repository with `gpgcheck=1`, so unsigned packages
  are refused.
- Minimal images may lack the `dnf` Python bindings for the interpreter
  Ansible uses; ansible-core switches to the system interpreter for package
  tasks, which the distribution provides.
