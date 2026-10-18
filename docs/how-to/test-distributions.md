---
title: How to test the distributions
type: how-to
audience: [developer]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Run the full distribution tests in disposable Incus VMs, record their results in the support matrix, and run the CI fast lane locally.
---

# How to test the distributions

`make test-distros` proves that the lab installs on every `supported` and
`tested` distribution: for each target in `tests/distros/targets.yml` it
launches a disposable Incus VM, bootstraps the lab from the CI answers file,
runs bootstrap a second time (it must change nothing), backs up once and runs
`pinka doctor --smoke`. Then it deploys the reference platform `refplat`
(`tests/reference-platform/`) in the VM and tests it as described in
[What the reference platform stages check](#what-the-reference-platform-stages-check).
The CI fast lane runs on every push instead, in a container per family:
`make check-mode`, the required job, checks the provider-agnostic roles in
check mode, and `make check-mode-extended`, a separate non-blocking job,
checks the whole provisioning play with the reference platform enabled.

!!! note "Prerequisites"
    - A developer machine with Incus and VM support (KVM), or a lab host whose
      lab is stopped (`sudo pinka stop`); the harness refuses to run while
      `pinka-lab.target` is active. The tests run one VM at a time, and each
      VM gets what preflight requires of a lab host: 8 vCPUs, 18 GiB of
      memory (preflight compares the guest's `MemTotal`, which the kernel's
      reservations keep below the assigned size, with 16 GiB) and a 300 GiB
      root disk (150 GiB free for `/var/lib` and 100 GiB for `/srv/pinka`).
      The machine needs those 8 threads and 18 GiB besides its own, and an
      Incus storage pool that allocates disks thinly (`dir`, `btrfs`, `zfs`
      or LVM thin), so a VM takes only the space it writes.
    - A checkout of the repository with the development tools (`make setup`).
    - An ephemeral, pre-approved auth key of the tailnet for tests, tagged
      `tag:pinka-qa`: the test VMs are staging labs and join it.

## Run the distribution tests

1. Commit what you want to test. The VMs get a clone of `HEAD`; uncommitted
   changes are not tested.

2. Export the auth key and run one target, or all of them without
   `TEST_DISTROS_ARGS`:

    <!-- qa:skip reason="launches VMs and joins a tailnet" -->
    ```bash
    export PINKA_TEST_TAILSCALE_AUTHKEY=<auth-key>
    make test-distros TEST_DISTROS_ARGS="--target debian-13 --accept-unverified-images"
    ```

    `<auth-key>` is the ephemeral key of the test tailnet. While the image
    aliases in `tests/distros/targets.yml` carry an unverified marker, the
    harness runs only with `--accept-unverified-images`. A full run takes
    two to three hours per target. Each VM is deleted when its test passes and
    kept for inspection when it fails (`--keep never|failed|always`); a kept
    VM is stopped, so that the next target's VM is the only one running
    (`incus start` it to inspect it, or pass `--keep-running`); logs,
    the bootstrap state files and the host profile are in
    `build/test-distros/<UTC time>/<target>/`.

3. List the targets and their last results:

    <!-- qa:run env=E0 -->
    ```bash
    PYTHONPATH=tools/test-distros:tools/platform:tools/lockcheck:preflight .venv/bin/python -m pinka_test_distros list
    ```

## What the reference platform stages check

After `smoke`, each VM runs these stages in order; a failed stage ends the
test and the rest count as skipped. The VMs have no GPU, so the training
stages use the CPU-only job `tunecpu`; serving an adapter with vLLM is left
to GPU hosts.

| Stage | What it does | Passes when |
| --- | --- | --- |
| `platform` | While nothing serves on the vLLM port (the VMs have no GPU), installs a stand-in for the inference API there: an enabled root unit (`pinka-test-inference-standin`) that answers `/health` and `/v1/models` like vLLM, so the health gate passes for a platform that requires `inference`, and that outlives the reboot of `resume`; the inference guard decides who reaches it, as it does for vLLM. Then runs bootstrap with the CI answers plus `refplat` and its content package `cases` from the VM's clone at the tested commit (`file:///root/pinka`) and `mode: modify`: the installer inspects the release and generates the passwords of its database roles and its `api-token` secret into the vault, as for any lab (the answers of each stage are kept as `<stage>-answers.yml` in the results) | `refplat` is the active platform, `refplat status` reaches its database, `pinka doctor --smoke` exits 0, and `refplat infer` returns the stand-in's model list through the broker (`inference-broker`): the inference API answers the broker |
| `isolation` | Reads the probe report of the agent (`untrusted-agent`), then runs the probe as a transient unit for each loopback-capable profile, with that profile's rendered hardening and groups: as the controller's and the broker's own accounts, and as a temporary account from the platform UID range for the profiles `refplat` has no service of | the agent reached nothing; no profile reached Prometheus, Loki or Tempo directly; the query proxy answered exactly the profiles that query; the inference API answered only the controller and the broker, and the GPU arbiter's and the run-network helper's sockets only the controller |
| `training` | `refplat dataset`, then `refplat train --job tunecpu --wait` and `refplat lineage` | the run succeeded and its lineage names the registered adapter |
| `resume` | Starts a slow run, waits for its third checkpoint and reboots the VM | the arbiter suspended the run at the shutdown, resumed it at boot, and the run succeeded |
| `redeploy` | Runs bootstrap again with the same release | it changes nothing |
| `replace` | Makes a copy of the release renamed `refplatb` in a repository of its own in the VM and deploys it from the answers with `--replace-platform refplat` | `refplatb` is active, its controller answers, and its agent's probe reached nothing |
| `restore` | `refplat dataset`, `pinka backup now`, removes a controller record, and runs bootstrap with the answers in `mode: restore` with `restore.snapshot: latest` (and `refplatb`) | the record is back, the platform answers, and `pinka doctor --smoke` exits 0 |

Besides the logs, a test's directory holds the probe reports of every
profile (`isolation.json`), the arbiter's transition log after the reboot
(`transitions.jsonl`) and the active platform after each deployment
(`*-active.json`). The per-profile expectations come from the profile flags
in `contract/profiles.yml`
([Platform privilege profiles](../reference/platform-profiles.md)).

## Record the results

1. The harness writes the latest result per target to
   `tests/distros/results.yml`: the date, the tested commit, the image
   fingerprint it used and the result of every stage. Regenerate the support
   matrix:

    <!-- qa:run env=E0 -->
    ```bash
    make generate
    ```

2. Commit `tests/distros/results.yml` and `docs/support-matrix.md` together.
   A result counts for 90 days; the *Last test results* table shows the date
   until which it is valid.

## Run the CI fast lane locally

The fast lane needs Docker and network access, and runs nothing on the host
itself: it builds a systemd container from a pinned base image of
`containers.ci_images` in `versions.lock.yml`, runs preflight inside it, and
runs a play with `--check` through Ansible's docker connection, the container
standing in for the host. It passes the variables bootstrap would pass (the
container counts as bare metal whose operator forced the installation, since
preflight classifies containers as unsupported), the example host variables
and the example vault with a local backup repository.

There are two runs, and CI has a job for each:

| Run | Play | Skips | In CI |
| --- | --- | --- | --- |
| `make check-mode` | `ansible/check-mode.yml`: the provider-agnostic roles `base`, `users_groups`, `ssh` and `layout` | none | required, blocking |
| `make check-mode-extended` | `ansible/site.yml`, the whole provisioning play, with the reference platform enabled as in the VMs | components whose sources are not verified yet | separate, non-blocking |

1. Run the required check:

    <!-- qa:skip reason="needs Docker" -->
    ```bash
    make check-mode CI_IMAGE=<image-key>
    ```

    `<image-key>` is a key under `containers.ci_images`, for example
    `debian_13` or `fedora_44`.

2. Run the extended check of the whole play:

    <!-- qa:skip reason="needs Docker" -->
    ```bash
    make check-mode-extended CI_IMAGE=<image-key>
    ```

    `<image-key>` is the same key as in item 1. Roles whose component source or package pins are not verified yet for the
    container's release are skipped by their tag, because the play refuses
    them. The run ends with a summary that lists every skipped component as
    *SKIPPED, not tested*, with the reason; a skipped component is never
    counted as passing. In CI the summary appears on the job's page. A skip
    disappears by itself once the component's sources are verified (see
    [How to update pinned versions](update-versions.md)).

    Components installed from vendor repositories (Docker, Incus, Tailscale,
    the NVIDIA Container Toolkit) are checked up to their repository: check
    mode downloads and verifies the repository key but cannot resolve
    packages from a repository it did not really add, so these roles end
    there in check mode. The reference platform is enabled with placeholder
    secrets: the platform role installs the deployment tool and sets up the
    sources; fetching, building and deploying the release need a real host
    and are tested by `make test-distros`.

The container is removed afterwards; the variables, the inventory, the
container's host profile and the extended run's summary stay in
`build/check-mode/`.

## Verify the tests

The harness's own tests run without Incus or Docker, against fake
command-line tools:

<!-- qa:run env=E0 -->
```bash
.venv/bin/pytest tools/test-distros
```

They check, among other things, that every `supported` and `tested` release
of the support matrix has a target and that every family has a CI image.
