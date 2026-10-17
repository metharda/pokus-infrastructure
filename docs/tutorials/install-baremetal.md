---
title: Install a lab on bare-metal Linux
type: tutorial
audience: [operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Turn a physical Linux machine with an NVIDIA GPU and a spare disk into a Pokus lab, including the driver, the reboots it needs and the dedicated storage pool.
---

# Install a lab on bare-metal Linux

In this tutorial you install a lab on a physical machine running Ubuntu 24.04
with an NVIDIA GPU and a second disk for the Incus storage pool. Compared with
WSL2, bare metal adds what you handle once: the NVIDIA driver, the reboots it
needs, and formatting the pool disk. At the end doctor accepts the lab.

!!! note "Prerequisites"
    - A machine with Ubuntu 24.04 installed (any other `supported` or
      `tested` release of the [support matrix](../support-matrix.md) works the
      same way), at least 16 GiB of memory and 8 CPU threads (24 GiB and 16
      threads recommended), 150 GiB free for `/var/lib` and 100 GiB for
      `/srv/pinka`.
    - An NVIDIA GPU with at least 8 GiB of memory for local inference. No
      driver needs to be installed: the lab installs a pinned one.
    - A second, empty disk for the storage pool (without one, choose a loop
      file in step 3 and skip what is said about the disk).
    - Physical access or a remote console for reboots: with Secure Boot on, a
      key enrollment screen can appear before the system starts.
    - Your account with `sudo` rights, a Tailscale account for the lab, and the
      SSH public keys of the researchers.
    - The address of the pokus-infrastructure repository (or of your lab's private fork).

## Step 1: Get the repository and check the machine

1. Clone the repository into your home directory:

    <!-- qa:skip reason="needs the repository address" -->
    ```bash
    git clone <repository-url> ~/pinka
    cd ~/pinka
    ```

    `<repository-url>` is the address of the pokus-infrastructure repository or your fork.

2. Ask preflight whether the machine can host the lab. It only reads:

    <!-- qa:run env=E1 -->
    ```bash
    ./preflight/pinka-preflight --no-write
    echo "exit code: $?"
    ```

    Exit code 0 means supported and 10 degraded (the summary lists the
    features that are off and why). Exit code 30 with the single action
    `secure_boot_mok` means that Secure Boot is on and no NVIDIA driver is
    installed: the driver can only be installed together with a Machine
    Owner Key (MOK), and that needs your consent, which you give in step 2.
    Any other code stops here (see
    [How to check whether a machine can host the lab](../how-to/check-a-host.md)).

3. Find the stable name of the pool disk:

    <!-- qa:run env=E1 -->
    ```bash
    ls -l /dev/disk/by-id/
    lsblk --output NAME,SIZE,MODEL,SERIAL,FSTYPE,MOUNTPOINTS
    ```

    Note the `/dev/disk/by-id/...` path whose target is the empty disk. Check
    the model and size twice: this disk is formatted in step 3.

## Step 2: Start the installer

Start bootstrap. It installs its pinned tools and hands the terminal to the
installer TUI:

<!-- qa:skip reason="interactive; provisions the host" -->
```bash
sudo ./bootstrap/bootstrap.sh
```

The Welcome and Preflight screens show what preflight found. With Secure
Boot on and no driver, the Preflight screen explains the MOK enrollment: tick
*Install the driver and enroll a MOK at the next boot*. Preflight runs again
and now plans the enrollment instead of stopping; the GPU stays off until
the driver is loaded, so `gpu_inference` is listed as missing. Accept each
missing feature that you can live with; [How to install a lab with the
installer TUI](../how-to/install-with-the-tui.md) explains every screen.

## Step 3: Answer the screens for bare metal

1. **Machine**: a name such as `pinka` and node role `full`.
2. **Storage**: choose the dedicated disk and type its full
   `/dev/disk/by-id/` path in the red dialog.

    !!! danger "Data loss"
        Everything on the confirmed disk is destroyed when the installation
        runs. Mounted and system disks cannot be chosen; a disk with existing
        signatures also needs you to type `WIPE`.

3. **GPU & inference**: keep the driver on `auto`; the installation installs
   the pinned driver only when none is loaded. With Secure Boot the screen
   asks for a MOK password: enter a one-time password twice and keep it until
   the next reboot, when you type it in the key enrollment screen. It is
   never stored. Choose the serving profile of your GPU class and a pinned
   default model.
4. **Researchers**, **Remote access**, **Data & backups**, **Golden images**
   and **Secrets & vault** as in the how-to.

On the Review screen check the storage line, which is shown in red because it
cannot be undone, then press **Install**.

## Step 4: Reboot when the installation asks for it

The installation stops with exit code 76 when the host must reboot: the
distribution asks for one after the driver or a kernel update, or the
installation has imported the driver's Machine Owner Key, which waits for
enrollment. The installer shows what to do and quits. Without exit code 76,
go on with step 5.

1. Reboot:

    <!-- qa:skip reason="reboots the host" -->
    ```bash
    sudo systemctl reboot
    ```

2. If the blue MOK Manager screen appears during the boot, choose
   *Enroll MOK*, *Continue*, *Yes*, enter the MOK password from step 3 and
   choose *Reboot*.

3. Log in again and continue the installation:

    <!-- qa:skip reason="interactive; provisions the host" -->
    ```bash
    cd ~/pinka
    sudo ./bootstrap/bootstrap.sh
    ```

    Choose **Resume** and enter the vault password. The installation goes on
    where it stopped. When Tailscale asks for a login, open the URL (or scan
    the QR code) and approve the machine in your tailnet.

Bootstrap reboots by itself only when you start it with `--allow-reboot`
([Bare-metal hosts](../providers/baremetal.md#secure-boot-and-reboots)).

!!! tip "Alternative: install the driver yourself"
    If you prefer your distribution's documented procedure for the NVIDIA
    driver and its Secure Boot key, install the driver before step 2, enroll
    its key at the next boot and run preflight again (step 1); it then finds
    the driver loaded. Choose *I install it myself* on the GPU screen. The
    installation keeps your driver and does not ask for a MOK password.

## Step 5: Check the lab

1. Run doctor:

    <!-- qa:run env=E2 -->
    ```bash
    sudo pinka doctor
    echo "exit code: $?"
    ```

    Exit code 0 means every check passed; 1 means warnings only, which is
    normal on a new lab (no backup yet, for example).
    [Fix a failed doctor check](../how-to/fix-doctor-checks.md) has one section
    per check.

2. Check that a container sees the GPU and that the pool is on the disk:

    <!-- qa:run env=E2 -->
    ```bash
    sudo pinka status
    sudo incus storage list
    ```

    `status` lists the GPU with its driver; the pool's source is the disk you
    chose.

3. Optionally run the smoke test, which starts a throwaway target of every
   golden image:

    <!-- qa:run env=E2 -->
    ```bash
    sudo pinka doctor --smoke
    ```

## What you built

A bare-metal lab host: the pinned NVIDIA driver (held, so automatic updates
never replace it), the Incus storage pool on its own disk, the research
accounts and space, Tailscale remote access, the lab services and telemetry,
the golden images, vLLM serving the default model, backups on a timer, and a
sleep inhibitor that keeps the machine awake while the lab is up
([power policy](../providers/baremetal.md#power-policy)).

Next:

- Let the researchers connect: [How to connect to the lab](../how-to/connect.md).
- Day-to-day work: [Operations](../operations.md).
