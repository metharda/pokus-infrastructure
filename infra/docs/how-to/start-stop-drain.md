---
title: How to start, stop and drain the lab
type: how-to
audience: [operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Start the lab, stop it without losing runs by draining the orchestrator first, and know what happens after a power loss.
---

# How to start, stop and drain the lab

The lab is up while `pinka-lab.target` is active: the Compose projects, the
sleep inhibitor (bare metal and VMs with `power_policy:
lab_session_inhibit`) and later the platform's services belong to it. Starting
it waits for the [health gate](../explanation/lab-session.md); stopping it with
`pinka stop` first lets running benchmark runs finish.

!!! note "Prerequisites"
    - A lab host provisioned by bootstrap (`pinka` is installed with it).
    - An admin account on the host (member of `pinka-admin`, so `sudo` works).

## Start the lab

1. Start the target:

    <!-- qa:run env=E2 -->
    ```bash
    sudo pinka start
    ```

    The command returns when the health gate has passed and this boot's session
    record exists. If the gate fails, the command fails too; see
    [Fix a failed doctor check](fix-doctor-checks.md#check-lab-health_gate).

2. The lab starts at boot unless `lab_session_autostart: false` is set in the
   host_vars file. With autostart off, start it by hand after every boot.

## Stop the lab and drain the orchestrator

1. Stop the lab:

    <!-- qa:run env=E2 -->
    ```bash
    sudo pinka stop --timeout 1800
    ```

    `pinka stop` asks the orchestrator to drain through its socket
    `/run/pinka/orch/api.sock`: it starts no new runs and waits until the
    running ones end. When none are left, or after `--timeout` seconds,
    `pinka-lab.target` and everything that belongs to it stop. Runs still active
    at the timeout are stopped with the lab and recorded as interrupted. Before
    the platform is deployed there is no orchestrator socket and nothing to
    drain.

2. To stop without waiting, give a timeout of 0. Use it only when no runs
   matter, for example on a staging host.

## What happens after a power loss

Nothing drains when the power fails. At the next boot:

- the lab starts again (with autostart), passes the health gate and writes a
  new session record with a new boot ID and a freshly computed environment
  fingerprint;
- runs of the previous boot cannot continue; the platform records them as
  interrupted, and their partial data stay in the database;
- PostgreSQL keeps every committed transaction (`fsync` and
  `synchronous_commit` are always on; doctor checks it);
- backup and database dump timers that missed their time run once the host is
  up (their timers are persistent).

On WSL the Linux side stops when Windows sleeps or shuts down; see
[WSL2 hosts](../providers/wsl2.md) for keeping it awake while the lab runs.

## Verify the lab state

1. Show the units, containers, Incus instances, GPU, disk and the last backup:

    <!-- qa:run env=E2 -->
    ```bash
    sudo pinka status
    ```

    After `start`, `pinka-lab.target` and its units are `active`; after `stop`,
    they are `inactive`.

2. After a start, run all checks:

    <!-- qa:run env=E2 -->
    ```bash
    sudo pinka doctor
    ```

    Doctor exits with 0 when everything passed (see
    [Doctor checks](../reference/doctor-checks.md)).
