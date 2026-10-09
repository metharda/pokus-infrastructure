---
title: How to update the host operating system safely
type: how-to
audience: [operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Update the lab host's packages with pinka update-host on every distribution family, keeping the pinned packages, and see what changed in the environment fingerprint.
---

# How to update the host operating system safely

Measurements depend on the software they ran on, so the lab never updates
itself behind your back: the pinned packages (Docker, the NVIDIA Container
Toolkit, Incus, Tailscale and, unless allowed, the kernel) are held, and
rolling releases get no automatic updates at all.
`pinka update-host` is the supported way to update everything else. It
drains and stops the lab, updates the operating system with the holds in
place, runs bootstrap (which re-applies the pins and ends with doctor) and
prints how the [environment fingerprint](../reproducibility.md) changed.

!!! note "Prerequisites"
    - A lab host provisioned by bootstrap, with the checkout bootstrap runs
      from.
    - An admin account on the host (member of `pinka-admin`).
    - A time when the lab may stop: running benchmark runs are drained first.
    - On a WSL host, Windows itself is updated separately; this page covers the
      Linux side only.

## Update the host

1. Run the update:

    <!-- qa:skip reason="updates the operating system of the lab host" -->
    ```bash
    sudo pinka update-host
    ```

    It asks for confirmation (`--yes` skips the question), then:

    1. records the current fingerprint;
    2. drains the orchestrator and stops `pinka-lab.target`, as in
       [How to start, stop and drain the lab](start-stop-drain.md);
    3. updates the packages with the family's package manager; held packages
       stay at their pinned versions:

        | Family | Commands | How packages are held |
        | --- | --- | --- |
        | Debian, Ubuntu | `apt-get update`, `apt-get --yes upgrade` | apt preferences pins |
        | Fedora, RHEL family | `dnf --assumeyes upgrade` | dnf versionlock |
        | openSUSE | `zypper --non-interactive update` | zypper locks |
        | Arch Linux | `pacman --sync --refresh --sysupgrade --noconfirm` | `IgnorePkg` |

    4. runs bootstrap, which converges the configuration, starts the lab again
       and ends with `pinka doctor`;
    5. prints every fingerprint field that changed, for example a new kernel
       release or Python version.

2. Read the fingerprint changes. Results measured before and after the update
   are comparable for latency and resource use only if the fingerprint hash
   is unchanged; see [Reproducibility](../reproducibility.md).

3. If the command printed `A reboot is required`, reboot when no runs are
   active. Bootstrap never reboots on its own. The notice comes from
   `/var/run/reboot-required`, which only the Debian family writes; on the
   other families check yourself:

    <!-- qa:skip reason="the answer depends on the installed updates" -->
    ```bash
    sudo dnf needs-restarting -r         # Fedora, RHEL family
    sudo zypper needs-rebooting          # openSUSE
    pacman -Q linux; uname -r            # Arch Linux: compare the versions
    ```

## Update the pinned packages themselves

`update-host` never moves a pinned package. To update Docker, Incus, the NVIDIA
Container Toolkit or Tailscale, change their versions in `versions.lock.yml`
as described in [How to update pinned versions](update-versions.md) and run
bootstrap. Kernel updates on bare metal are held unless the host_vars file
sets `allow_kernel_updates: true`.

## Verify the update

1. Check the lab:

    <!-- qa:run env=E2 -->
    ```bash
    sudo pinka doctor
    ```

    Doctor exits with 0 or 1 (warnings only). Fix failed checks with
    [How to fix a failed doctor check](fix-doctor-checks.md).

2. Show the new fingerprint hash:

    <!-- qa:run env=E2 -->
    ```bash
    sudo pinka fingerprint
    ```

    The next session record carries this hash.
