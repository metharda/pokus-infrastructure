---
title: How to start, stop and drain the lab
type: how-to
audience: [operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Start the lab and its platform, stop it without losing work by running the platform's drain hook first, and know what happens after a power loss.
---

# How to start, stop and drain the lab

The lab is up while `pinka-lab.target` is active: the Compose projects, the
sleep inhibitor (bare metal and VMs with `power_policy:
lab_session_inhibit`) and the platform's `pinka-platform.target` belong to it.
Starting it waits for the [health gate](../explanation/lab-session.md) and the
platform's `health` hook; stopping it with `pinka stop` first runs the
platform's `drain` hook, so its work ends cleanly or is recorded as
interrupted. Infra knows the platform only through these hooks; each runs in
its own transient unit `pinka-hook@<platform>-<hook>-<id>.service` under the
platform controller's account and hardening, and writes its diagnostics to
the journal.

!!! note "Prerequisites"
    - A lab host provisioned by bootstrap (`pinka` is installed with it).
    - An admin account on the host (member of `pinka-admin`, so `sudo` works).

## Start the lab

1. Start the target:

    <!-- qa:run env=E2 -->
    ```bash
    sudo pinka start
    ```

    The command returns when the health gate has passed, this boot's session
    record exists and the platform's `health` hook answers ok. Before the
    platform's services start, its `reconcile` hook runs once (the unit
    `<platform>-reconcile.service`); if it fails, no service starts. If the
    gate fails, the command fails too; see
    [Fix a failed doctor check](fix-doctor-checks.md#check-lab-health_gate).
    If the platform stays unhealthy for `lab.health_wait` seconds (300 by
    default, `doctor_health_wait` in the host_vars file), the command says
    so and exits with 1; the lab keeps running.

2. The lab starts at boot unless `lab_session_autostart: false` is set in the
   host_vars file. With autostart off, start it by hand after every boot.

## Stop the lab and drain the platform

1. Stop the lab:

    <!-- qa:run env=E2 -->
    ```bash
    sudo pinka stop --timeout 1800
    ```

    `pinka stop` runs the platform's `drain` hook with `--timeout 1800`: the
    platform starts no new work and waits until the running work ends. The
    hook answers when it is done; then `pinka-platform.target` stops, then
    `pinka-lab.target` and everything else that belongs to it. Without
    `--timeout`, the drain gets `lab.drain_timeout` seconds (1800 by default,
    `doctor_drain_timeout` in the host_vars file). If the drain fails or
    does not answer in time, systemd stops the hook and the services stop
    anyway; the platform records what was interrupted. Without a platform
    there is nothing to drain.

    After the drain, `pinka stop` asks the GPU arbiter for idle. A training
    run of a resumable job writes its checkpoint and is suspended: the GPU
    stays reserved for it, and it resumes from its checkpoints when the lab
    starts again, with `pinka start` or after a reboot (the resume is logged
    as a transition; `sudo pinka gpu status` shows it). Any other job ends
    failed. `sudo pinka gpu idle` ends a suspended run for good.

2. To stop with the shortest drain, give a timeout of 1 second. Use it only
   when no work matters, for example on a staging host.

## What happens after a power loss

Nothing drains when the power fails. At the next boot:

- the lab starts again (with autostart), passes the health gate and writes a
  new session record with a new boot ID and a freshly computed environment
  fingerprint;
- runs of the previous boot cannot continue; the platform records them as
  interrupted, and their partial data stay in the database; a training run
  of a resumable job resumes from its last checkpoint, as after `pinka stop`
  and `pinka start`;
- PostgreSQL keeps every committed transaction (`fsync` and
  `synchronous_commit` are always on; doctor checks it);
- backup and database dump timers that missed their time run once the host is
  up (their timers are persistent).

On WSL the Linux side stops when Windows sleeps or shuts down; see
[WSL2 hosts](../providers/wsl2.md) for keeping it awake while the lab runs.

## Verify the lab state

1. Show the units, the platform and its health, containers, Incus
   instances, GPU, disk and the last backup:

    <!-- qa:run env=E2 -->
    ```bash
    sudo pinka status
    ```

    After `start`, `pinka-lab.target`, `pinka-platform.target` and their
    units are `active` and the platform's health is `healthy` (the `health`
    hook runs once; without root it is not run); after `stop`, they are
    `inactive`.

2. After a start, run all checks:

    <!-- qa:run env=E2 -->
    ```bash
    sudo pinka doctor
    ```

    Doctor exits with 0 when everything passed (see
    [Doctor checks](../reference/doctor-checks.md)).
