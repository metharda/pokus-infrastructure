---
title: How to validate a platform release
type: how-to
audience: [developer]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Check a platform release against contract v2 with pinka-platform before you tag it.
---

# How to validate a platform release

A platform plugs into the lab through one file, `platform-manifest.yaml`, at
the root of its release (contract v2). Infra checks every release before
anything on the host changes and refuses it with every reason at once.
Run the same check on your release before you tag it.

!!! note "Prerequisites"
    - A development checkout of this repository with `make setup` done.
    - Your platform release as a directory: the subtree of your repository
      that a deployment extracts, with `platform-manifest.yaml` at its root.

## Check the release

1. Run the validator on the release directory:

    <!-- qa:run env=E0 -->
    ```bash
    cd <checkout>/infra
    PYTHONPATH=tools/platform .venv/bin/python -m pinka_platform validate <release> --no-host-check
    ```

    `<checkout>` is the directory where you cloned this repository and
    `<release>` your release directory. `--no-host-check` skips the
    comparison with the accounts, groups and commands of your own machine;
    on the lab host, bootstrap runs the check with it.

2. Read the result. A valid release prints a summary and exits 0:

    <!-- qa:skip reason="example output" -->
    ```text
    platform refplat 0.1.0 (python-uv)
    requires: postgres, telemetry, gpu
    services:
      ctl: controller as refplat-ctl (Incus bench/build; restricted data)
      agent: untrusted-agent as refplat-agent
    job tune: docker.io/library/python@sha256:<digest> (1 GPU)
    socket ctl/api: researchers, root
    socket ctl/session: untrusted-agent  [dedicated to the agent]
    content cases (restricted)
    cli: refplat
    ```

    `<digest>` stands for the job image's pinned digest.

    A refused release prints one line per problem and exits 65:

    <!-- qa:skip reason="example output" -->
    ```text
    services[1].secrets: untrusted-agent has no secrets [secrets-forbidden]
    refused: 1 problem(s)
    ```

    Each line names the place in the manifest, what is wrong and the rule.
    [Platform manifest](../reference/platform-manifest.md) describes every
    field and the reserved names, and
    [Platform privilege profiles](../reference/platform-profiles.md) what
    each profile may do and who may use whose sockets.

3. After `uv sync --frozen` in the release (runtime `python-uv`), check
   again with `--built`: the commands below `.venv/` must exist then.

    <!-- qa:run env=E0 -->
    ```bash
    PYTHONPATH=tools/platform .venv/bin/python -m pinka_platform validate <release> --no-host-check --built
    ```

    `<release>` is the same release directory as in step 1.

## Verify

The command exits 0 and prints the summary. `--json` prints the same result
as JSON for a CI job. Every option is in
[pinka-platform command](../reference/cli/pinka-platform.md).
