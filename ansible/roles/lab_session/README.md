# lab_session

`pinka-lab.target`, the startup health gate and the session record of every
boot (BUILD_PROMPT §7.16, `docs/contracts.md`); tag `lab_session`.

- `pinka-lab.target`: the lab is up while it is active. The lab's units
  (Compose projects, the sleep inhibitor of `power_policy:
  lab_session_inhibit`, later the platform's services) are `WantedBy` and
  `PartOf` it. `lab_session_autostart` makes `multi-user.target` want it.
- `pinka-health-gate.service` runs `/usr/local/libexec/pinka/health-gate`
  with `/etc/pinka/health-gate.json` and repeats the checks until all pass or
  `lab_session_gate_timeout` expires:
  - time: `timedatectl` reports NTP synchronization; on WSL the clock is also
    compared with `lab_session_ntp_reference` (SNTP), skipped when the server
    does not answer;
  - postgres: the container `pinka-postgres` is healthy (`full`, `lab`);
  - collector: the gateway's health endpoint answers 200;
  - incus: `incus query /1.0` succeeds (`full`, `lab`);
  - gpu: with `gpu_inference` on `full` and `inference` nodes, or with
    `gpu_training` (after the switches, BUILD_PROMPT §2.3), a container of
    the pinned CUDA image lists a GPU, with the GPU method of
    `/etc/docker/daemon.json` (CDI or the NVIDIA runtime).
  The result goes to `/run/pinka/health-gate.json`. Platform services
  `Require` and order themselves after this unit (contracts.md).
- `pinka-session-start.service` runs `/usr/local/libexec/pinka/session-start`
  once per boot: `/var/lib/pinka/sessions/<boot_id>.json` (0644 root) with the
  boot ID, start time, environment fingerprint and hash
  (`pinka fingerprint --json`) and the outcome of a fresh preflight
  (`pinka preflight --json`). A failed command leaves its field `null`
  and its reason under `errors`; the record is written anyway.
- The ordered shutdown (`pinka stop`, running the active platform's `drain`
  hook first once infra runs hooks, plan step 3d.4) is part of the `pinka`
  CLI.

## Variables

See `meta/argument_specs.yml`.

## Feature flags

`gpu_inference` or `gpu_training` adds the GPU check; `node_role` selects the PostgreSQL and
Incus checks.

## Idempotency

Files and units are only rewritten when their content changes; starting the
target when it is active does nothing, and a boot's session record is never
rewritten.
