---
title: Install a lab on WSL2
type: tutorial
audience: [operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Turn a Windows workstation with an NVIDIA GPU into a complete Pokus lab inside a WSL2 distribution, the reference setup, and check it with doctor.
---

# Install a lab on WSL2

In this tutorial you build the reference setup of Pokus Infrastructure (pinka): a lab inside an
Ubuntu 24.04 distribution of WSL2 on a Windows workstation with an NVIDIA GPU.
You prepare WSL on the Windows side, check the distribution with preflight,
install the lab with the installer TUI, and finish with a doctor run that
accepts the lab.

Bootstrap never changes anything on the Windows side: every Windows step in
this tutorial is one you run yourself.

!!! note "Prerequisites"
    - A Windows workstation with WSL2, at least 16 GiB of memory and 8 CPU
      threads to give to WSL (24 GiB and 16 threads recommended), and room
      for the WSL virtual disk: preflight wants 150 GiB free for `/var/lib`
      and 100 GiB for `/srv/pinka` (400 GiB and 500 GiB recommended).
    - For local inference: an NVIDIA GPU with at least 8 GiB of memory and the
      current NVIDIA driver for Windows. Inside WSL nothing else is installed
      for the GPU.
    - A Tailscale account for the lab, and the SSH public keys of the
      researchers.
    - The address of the pokus-infrastructure repository (or of your lab's private fork).

## Step 1: Create the WSL distribution

1. In a Windows terminal, list the distributions WSL offers and install
   Ubuntu 24.04:

    <!-- qa:skip reason="runs on the Windows side" -->
    ```powershell
    wsl --list --online
    wsl --install -d <distro>
    ```

    `<distro>` is the name of Ubuntu 24.04 in the list. Create the Linux user
    when WSL asks; this is your own account, not a lab account.

2. Give WSL enough memory and CPUs. Copy `contrib/windows/wslconfig.template`
   from the repository to `%UserProfile%\.wslconfig`, set `memory` and
   `processors` to what the lab may use, then restart WSL:

    <!-- qa:skip reason="runs on the Windows side" -->
    ```powershell
    wsl --shutdown
    ```

    The template's comments explain each setting; [WSL2 hosts](../providers/wsl2.md#recommended-wslconfig)
    explains the values Pokus Infrastructure recommends.

You now have an Ubuntu 24.04 distribution with the resources of the lab.

## Step 2: Get the repository and check the distribution

1. Open the distribution and clone the repository into your home directory:

    <!-- qa:skip reason="needs the repository address" -->
    ```bash
    git clone <repository-url> ~/pinka
    cd ~/pinka
    ```

    `<repository-url>` is the address of the pokus-infrastructure repository or your fork.

2. Ask preflight whether the distribution can host the lab. It only reads:

    <!-- qa:run env=E1 -->
    ```bash
    ./preflight/pinka-preflight --no-write
    echo "exit code: $?"
    ```

    The summary names the environment (`wsl2`), the distribution and every
    finding. Exit code 0 means supported and 10 degraded (for example no
    `/dev/kvm` for VM targets, so the lab runs container targets only). 30
    means an action is needed; on a distribution where systemd is not running
    yet, bootstrap offers to enable it in the next step. Any other code stops
    here; the summary says why (see
    [How to check whether a machine can host the lab](../how-to/check-a-host.md)).

## Step 3: Install the lab

1. Start bootstrap. It installs its pinned tools and hands the terminal to the
   installer TUI:

    <!-- qa:skip reason="interactive; provisions the host" -->
    ```bash
    sudo ./bootstrap/bootstrap.sh
    ```

    If systemd is not running in the distribution yet, bootstrap first asks
    whether to enable it in `/etc/wsl.conf`. Answer `y`; it stops with exit
    code 75 and prints the restart command. Restart the distribution as in
    item 3 below and start bootstrap again.

2. Go through the screens as described in
   [How to install a lab with the installer TUI](../how-to/install-with-the-tui.md).
   For this setup choose:
    - **Machine**: a name such as `pinka`, node role `full`.
    - **Resources**: the memory and processors you set in `.wslconfig`.
    - **Storage**: a loop file (WSL has no dedicated disk).
    - **GPU & inference**: no driver (WSL uses the Windows driver), the serving
      profile of your GPU class and a pinned default model.

3. On the Review screen press **Install**. The installation writes the rest
   of `/etc/wsl.conf` (no Windows drives, no Windows programs, the machine
   name), which needs a WSL restart: the installer shows exit code 75 and the
   command. In a Windows terminal run:

    <!-- qa:skip reason="runs on the Windows side" -->
    ```powershell
    wsl --terminate <distro>
    ```

    `<distro>` is the name of the distribution, as in step 1. Open the
    distribution again and continue the installation:

    <!-- qa:skip reason="interactive; provisions the host" -->
    ```bash
    cd ~/pinka
    sudo ./bootstrap/bootstrap.sh
    ```

    Choose **Resume** and enter the vault password. The installation goes on
    where it stopped; the progress screen shows each role.

4. When Tailscale asks for a login, open the URL (or scan the QR code) and
   approve the machine in your tailnet.

The installation ends on the Doctor screen.

## Step 4: Check the lab

1. Run doctor:

    <!-- qa:run env=E2 -->
    ```bash
    sudo pinka doctor
    echo "exit code: $?"
    ```

    Exit code 0 means every check passed; 1 means warnings only, which is
    normal on a new lab (no backup yet, for example). Each finding names its
    check; [Fix a failed doctor check](../how-to/fix-doctor-checks.md) has one
    section per check.

2. See what runs:

    <!-- qa:run env=E2 -->
    ```bash
    sudo pinka status
    ```

    The output lists the units of `pinka-lab.target`, the containers, the
    Incus instances, the GPU and the last backup.

3. Copy the `.wslconfig` that Pokus Infrastructure recommends for this machine to Windows:
   `/srv/pinka/services/contrib/wslconfig.recommended` holds it (from
   Windows it is reachable below `\\wsl$\<distro>\`). Restart WSL with
   `wsl --shutdown` after you replaced `%UserProfile%\.wslconfig`.

## What you built

A lab host inside WSL2: the research accounts and the shared research space,
Tailscale remote access, Docker with the lab services and the telemetry stack,
Incus for the benchmark targets with the golden images, vLLM serving the
default model on the GPU, backups on a timer, and `pinka` to operate it.

Next:

- Let the researchers connect: [How to connect to the lab](../how-to/connect.md).
- Keep WSL running while experiments run: the optional keep-alive helper and
  the known limitations are in [WSL2 hosts](../providers/wsl2.md).
- Day-to-day work: [Operations](../operations.md).
