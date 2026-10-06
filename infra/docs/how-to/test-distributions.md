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
`pokus-lab doctor --smoke`. The CI fast lane, `make check-mode`, runs on
every push instead and only checks the provider-agnostic roles in check mode
in a container.

!!! note "Prerequisites"
    - A developer machine with Incus (VM support, about 8 GiB of memory and
      60 GiB of disk per test), or a lab host whose lab is stopped
      (`sudo pokus-lab stop`); the harness refuses to run while
      `pokus-lab.target` is active.
    - A checkout of the repository with the development tools (`make setup`).
    - An ephemeral, pre-approved auth key of the tailnet for tests, tagged
      `tag:pokus-qa`: the test VMs are staging labs and join it.

## Run the distribution tests

1. Commit what you want to test. The VMs get a clone of `HEAD`; uncommitted
   changes are not tested.

2. Export the auth key and run one target, or all of them without
   `TEST_DISTROS_ARGS`:

    <!-- qa:skip reason="launches VMs and joins a tailnet" -->
    ```bash
    export POKUS_TEST_TAILSCALE_AUTHKEY=<auth-key>
    make test-distros TEST_DISTROS_ARGS="--target debian-13 --accept-unverified-images"
    ```

    `<auth-key>` is the ephemeral key of the test tailnet. While the image
    aliases in `tests/distros/targets.yml` carry an unverified marker, the
    harness runs only with `--accept-unverified-images`. A full run takes
    one to two hours per target. Each VM is deleted when its test passes and
    kept for inspection when it fails (`--keep never|failed|always`); logs,
    the bootstrap state files and the host profile are in
    `build/test-distros/<UTC time>/<target>/`.

3. List the targets and their last results:

    <!-- qa:run env=E0 -->
    ```bash
    PYTHONPATH=tools/test-distros:preflight .venv/bin/python -m pokus_test_distros list
    ```

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

The fast lane needs Docker and runs nothing on the host itself: it builds a
systemd container from a pinned base image of `containers.ci_images` in
`versions.lock.yml`, runs preflight inside it, and runs
`ansible/check-mode.yml` with `--check` through Ansible's docker connection.

<!-- qa:skip reason="needs Docker" -->
```bash
make check-mode CI_IMAGE=<image-key>
```

`<image-key>` is a key under `containers.ci_images`, for example `debian_13`
or `fedora_44`. The container is removed afterwards.

## Verify the tests

The harness's own tests run without Incus or Docker, against fake
command-line tools:

<!-- qa:run env=E0 -->
```bash
.venv/bin/pytest tools/test-distros
```

They check, among other things, that every `supported` and `tested` release
of the support matrix has a target and that every family has a CI image.
