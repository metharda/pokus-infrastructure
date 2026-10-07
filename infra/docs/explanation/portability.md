---
title: Portability across distributions and environments
type: explanation
audience: [operator, developer]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: How one set of roles provisions Debian, Red Hat, SUSE and Arch family hosts on bare metal, VMs and WSL2.
---

# Portability across distributions and environments

Pokus installs on mainstream systemd and glibc Linux distributions, on bare
metal, in virtual machines and under WSL2, with or without a GPU. It does
this with one set of Ansible roles, not one per distribution. This page
explains how the differences between hosts are contained so that the lab
behaves the same everywhere, and where they are recorded when they cannot be
removed.

## Families, not distributions

Preflight sorts every host into one of four **families**: `debian` (Debian,
Ubuntu and their derivatives), `redhat` (Fedora, RHEL, AlmaLinux, Rocky Linux,
CentOS Stream, Oracle Linux), `suse` (openSUSE Leap and Tumbleweed, SLES) and
`archlinux` (Arch Linux and its derivatives). A family shares a package
manager, a way to hold package versions, service names and file locations.
Which distributions and releases are accepted, and in which tier, is decided
by the [support matrix](../support-matrix.md).

A derivative is provisioned as its parent: Linux Mint 22 is treated as
Ubuntu 24.04 because its `/etc/os-release` names `noble` as its Ubuntu
codename. Repository URLs and version pins always use the parent's identity,
never the derivative's own codename.

## Where differences may live

Distribution differences are allowed in exactly four places:

1. `preflight/support-matrix.yml`: which distributions and releases are
   accepted, and in which tier.
2. `ansible/component-sources.yml`: where each host component comes from on
   each distribution ([Component sources](../reference/component-sources.md)).
3. `ansible/roles/<role>/vars/<family>.yml`: package names, service names,
   file paths and tool names for one family.
4. Small `tasks/<name>_<family>.yml` files, only where an Ansible module differs
   between families (for example the automatic-update setup).

The `platform_facts` role runs first in every play and is the only code that
interprets the distribution. It reads the host profile that preflight wrote,
so preflight and Ansible can never disagree about the host, and sets the
facts every other role uses:

| Fact | Meaning |
| --- | --- |
| `pokus_family` | `debian`, `redhat`, `suse` or `archlinux` |
| `pokus_distro`, `pokus_release`, `pokus_parent_release` | the distribution identity; for derivatives, the parent's |
| `pokus_release_key` | the key of the release in `versions.lock.yml`, for example `ubuntu-24.04` or `el-9`; empty on rolling releases |
| `pokus_release_model` | `fixed`, `fast` or `rolling` |
| `pokus_source_group` | the row of the component source matrix: `ubuntu`, `debian`, `fedora`, `rhel_like`, `opensuse` or `arch` |
| `pokus_pkg_manager` | `apt`, `dnf`, `zypper` or `pacman` |
| `pokus_version_hold_method` | how package versions are held (see below) |
| `pokus_service_names` | the family's names for the SSH and time synchronization services |
| `pokus_initramfs_tool` | `update-initramfs`, `dracut` or `mkinitcpio` |
| `pokus_provider` | `baremetal`, `wsl2` or `vm` |
| `pokus_firewall_backend` | `nftables`, `firewalld` or `ufw` |
| `pokus_mac` | `selinux`, `apparmor` or `none` |

No other role may read Ansible's distribution facts (`ansible_distribution`,
`ansible_os_family`, `ansible_pkg_mgr`, ...). `make lint` enforces this with a
check that fails on any such reference outside `platform_facts`.

## Installing and holding packages

Roles never call a package module directly. They ask the `pkg` role to
install a list of packages with the versions pinned in `versions.lock.yml`
for the host's release key, and to hold them afterwards:

| Family | Install | Hold |
| --- | --- | --- |
| debian | `apt`, `name=version` | apt preferences pins in `/etc/apt/preferences.d/pokus-pins` |
| redhat | `dnf`, `name-version` | `dnf versionlock` |
| suse | `zypper`, `name=version` | `zypper addlock` |
| archlinux | `pacman`, unpinned | `IgnorePkg` in `/etc/pacman.conf` |

Vendor repositories are added the same way everywhere: the key is
downloaded, its primary key fingerprint is compared with the one pinned in
`versions.lock.yml`, and only then installed as a `signed-by` keyring (apt)
or with `rpm --import` and `gpgcheck=1` (dnf, zypper). A release without
verified pins, or a source marked unverified in the component source matrix,
stops the play with a message instead of installing something unpinned.

Rolling releases (openSUSE Tumbleweed, Arch Linux) cannot be pinned: packages
are installed at the current version, held, and their actual versions go
into the environment fingerprint. Automatic updates are off on rolling
releases; fixed releases get security-only updates without automatic reboots,
and held packages are excluded.

## What stays the same everywhere

Most of the lab does not depend on the host distribution at all:

- Lab services run as **containers pinned by digest**.
- The platform runs in a **uv-managed Python environment** with a pinned
  CPython; the distribution's Python is never needed for Pokus code.
- **Targets are Incus instances built from Pokus's own golden images**, so the
  userland of a target is the same on every host. Only the host kernel
  differs, and it is recorded in the fingerprint.

## Environments: the provider roles

Everything that depends on how the host runs lives in exactly one provider
role, chosen from the host profile:

- [WSL2](../providers/wsl2.md): systemd and interop settings in
  `/etc/wsl.conf`, the GPU from the Windows driver, a recommended `.wslconfig`.
- [Bare metal](../providers/baremetal.md): the NVIDIA driver with the Secure
  Boot flow, reboot handling and the power policy.
- [Virtual machines](../providers/vm.md): the hypervisor's guest agent and, with
  GPU passthrough, the bare-metal driver handling.

The other roles are provider-agnostic: they read features from the host
profile and never detect anything themselves.

## Firewall and mandatory access control

The firewall role produces one effective policy with whichever backend the
host already uses, instead of replacing it: firewalld if it is active, else
ufw if it is active, else Pokus's own nftables table. SELinux stays
enforcing on hosts that use it; the `mac` role labels the research space so
containers can use it. Both are described in
[How to configure the firewall backend and SELinux](../how-to/firewall-and-selinux.md).

## How portability is proven

Two test layers cover the families. `make test-distros` bootstraps the whole
lab in a disposable Incus VM for every `supported` and `tested` release and
for the firewall and SELinux variants; a second bootstrap must change
nothing, and `pokus-lab doctor --smoke` must pass. The results, with their
dates, are in the [support matrix](../support-matrix.md#last-test-results)
and count for 90 days. On every push, the CI fast lane runs the
provisioning play in check mode in a systemd container per family, which
catches package names, repository keys and module arguments that do not fit
a family without provisioning anything. Roles whose source is still
unverified for a release are skipped there, as the play refuses them.
[How to test the distributions](../how-to/test-distributions.md) has the
commands.

Check mode changes nothing, so on a fresh host a unit, file, account or
directory that an earlier task would create is still missing when a later
task needs it. `platform_facts` therefore records in check mode what exists
when the play starts (`pokus_present`: systemd units, accounts and the
commands of the components from vendor repositories), and such later tasks
carry `when: not ansible_check_mode or <it exists>`; a task that needs a
directory created earlier in the run skips in check mode while that task
reports a change. Outside check mode these conditions are always true. Check
mode cannot resolve packages from a vendor repository that it did not really
add, so the roles of those components end after their repository step
(`meta: end_role`) while the component is not installed, and so does the
images role while Incus is not installed. A test walks every task that
`site.yml` reaches and fails when one of them manages a unit, `lineinfile`
target, authorized key, ACL, key file or download without such a condition.

## When a host cannot be supported

Immutable distributions, non-systemd distributions, musl-based
distributions, containers and WSL1 are unsupported. The way out is the same
for all of them: run a supported distribution in a virtual machine on that
host and provision the VM with the `vm` provider, with GPU passthrough if the
hypervisor offers it. The checklist for adding a new family is in
[How to add a distribution family](../distros/adding-a-family.md); the
family pages ([Debian](../distros/debian.md), [Red Hat](../distros/redhat.md),
[SUSE](../distros/suse.md), [Arch](../distros/archlinux.md)) list the
specifics and known issues of each.

Related decisions: [ADR-0003 (provider model)](../adr/0003-provider-model.md),
[ADR-0004 (family-based portability)](../adr/0004-family-based-portability.md),
[ADR-0005 (uv-managed Python)](../adr/0005-uv-managed-python.md) and
[ADR-0006 (firewall backend integration)](../adr/0006-firewall-backend-integration.md).
