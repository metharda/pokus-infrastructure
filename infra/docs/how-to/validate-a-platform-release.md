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
    requires: postgres, telemetry, inference, gpu
    services:
      ctl: controller as refplat-ctl (Incus bench/build; restricted data)
        state records: backed up
        state datasets: not backed up
      agent: untrusted-agent as refplat-agent
      broker: inference-broker as refplat-broker
    database: backed up
    job tune: public.ecr.aws/docker/library/python@sha256:<digest> (1 GPU, 2 CPUs, memory 4G, 600 s, resumable)
    job tunecpu: public.ecr.aws/docker/library/python@sha256:<digest> (0 GPU, 2 CPUs, memory 4G, 600 s, resumable)
    socket ctl/api: researchers, root
    socket ctl/session: untrusted-agent  [dedicated to the agent]
    socket broker/infer: researchers, root
    content cases (restricted)
    cli: refplat
    ```

    `<digest>` stands for the job image's pinned digest. This is the
    summary of the reference platform in `tests/reference-platform/`, a
    complete release to compare yours with.

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

3. With runtime `python-uv`, build the release as the deployment does and
   check again with `--built`: the commands below `.venv/` must exist then,
   and the agent's own environment may hold only its package and its locked
   dependencies.

    <!-- qa:run env=E0 -->
    ```bash
    cd <release>
    uv sync --frozen --all-packages --no-dev --no-editable
    UV_PROJECT_ENVIRONMENT=.agent-venvs/<service> uv sync --frozen --package <package> --no-dev --no-editable
    cd <checkout>/infra
    PYTHONPATH=tools/platform .venv/bin/python -m pinka_platform validate <release> --no-host-check --built
    ```

    `<release>` and `<checkout>` are the directories of step 1,
    `<service>` the name of your `untrusted-agent` service and `<package>`
    the project name of its package. The release you tag never contains
    `.venv` or `.agent-venvs`: the deployment builds both.

## Prepare for the deployment

1. With runtime `python-uv` and a `[build-system]` in `pyproject.toml`, lock
   the build backend in a dependency group named `build`:

    <!-- qa:skip reason="example of a pyproject.toml section" -->
    ```toml
    [dependency-groups]
    build = ["<backend>"]
    ```

    `<backend>` is the package of your `build-backend`, as in
    `[build-system] requires`. Run `uv lock` afterwards. The deployment
    installs the locked wheels with network access first, without building
    or running anything, and then builds your release without network, so
    the backend must come from `uv.lock`. Every locked dependency must
    therefore be available as a wheel.

2. Give your `untrusted-agent` service a package of its own and name its
   directory in the manifest:

    <!-- qa:skip reason="example of a manifest entry" -->
    ```yaml
    - name: agent
      profile: untrusted-agent
      package: packages/<agent-package>/
      start: [.venv/bin/<agent-command>]
    ```

    `<agent-package>` is the directory of the agent's workspace member and
    `<agent-command>` its console script. Of the release, the agent sees only
    this directory and an environment of its own that the deployment builds
    from it, with only that package and its locked dependencies; `.venv/` in
    its start command means that environment. Every dependency of the agent
    package must come from a package index: a dependency on another package
    of your release, also through other packages, or on a path, Git or URL
    source is refused, because it would put code the agent must not see into
    its environment. Move what the agent needs from shared packages into its
    own package. With runtime `static`, `package` is the directory the agent
    runs from, and its start command lies inside it.

3. Let your services take their sockets from systemd instead of creating
   them. Infra creates every socket of the manifest with its owner, group,
   mode and ACL and passes it to the serving service by socket activation:
   the file descriptors start at 3, `LISTEN_FDS` gives their number and
   `LISTEN_FDNAMES` their socket names from the manifest.

4. Keep each service's configuration schema (`config`) to the JSON Schema
   keywords of the manifest schema plus annotations (`$comment`,
   `default`, `examples`, `format`, `deprecated`, `readOnly`,
   `writeOnly`); a schema with another keyword is refused, because infra
   would not check it.

5. Keep a policy bundle (`policy.bundle`) out of OPA's own namespaces: no
   module may declare a package below `system` or `pinka`, and no data file
   may sit below a `system/` or `pinka/` directory or put those keys at the
   top of `data`.

## Verify

The command exits 0 and prints the summary. `--json` prints the same result
as JSON for a CI job. Every option is in
[pinka-platform command](../reference/cli/pinka-platform.md).
