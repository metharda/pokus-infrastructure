---
title: How to deploy a platform
type: how-to
audience: [operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Point the lab at a platform release and its content packages, check the release before it changes anything, deploy it and update it.
---

# How to deploy a platform

A lab runs at most one platform. Infra deploys it from its release and its
content packages (contract v2): bootstrap fetches each source repository
into a root-only mirror, extracts the release and every package without
`.git`, validates them against the contract, builds the release in a
sandbox, and publishes it read-only below
`/srv/pinka/platforms/<platform>/`. Restricted content packages go to
`/srv/pinka/restricted/<platform>/` only. A refused release changes nothing
on the host. The platform runs on `full` and `lab` nodes only.

!!! note "Prerequisites"
    - A lab host provisioned by bootstrap (`node_role: full` or `lab`).
    - The platform's release in a Git repository the host can reach by SSH,
      anonymous HTTPS or as a root-owned local repository, and its content
      packages (the manifest's `content`) likewise.
    - The lab's private configuration (the host_vars file of this host).

## Name the sources

1. Set the release and its content packages in the host_vars file:

    <!-- qa:skip reason="example of a host_vars file" -->
    ```yaml
    platform:
      repo: git@github.com:example-org/pokus-platform.git
      ref: platform-v<version>
      path: platform
      tag_prefix: platform-v
    content:
      catalog:
        repo: git@github.com:example-org/pokus-platform.git
        ref: catalog-v<version>
        path: catalog
        tag_prefix: catalog-v
    require_signed_tags: true
    pinka_allowed_signers:
      - "researcher1@example.com ssh-ed25519 <public key>"
    ```

    `<version>` is the release version in the tag, `<public key>` the
    release signer's SSH public key. `path` is the release's or package's
    subdirectory (`""` for the repository root) and `tag_prefix` the prefix
    of its release tags. A ref is a release tag (recommended), another tag,
    a branch or a full commit. With `require_signed_tags: true` only release
    tags with a valid SSH signature of an allowed signer are deployed.

    An `https://` URL must allow anonymous reads; a URL with credentials is
    refused. For an SSH host other than GitHub, add its host keys as
    `known_hosts` lines in `pinka_source_known_hosts`. A release and a
    package from the same repository must not overlap: neither path may
    contain the other, and the repository root contains every path.

2. With an SSH source, run the check of the next section once. It generates
   a deploy key on the host and prints its public key; add it to the
   repository as a read-only deploy key.

## Check the release

1. Fetch, stage and validate the release without changing anything else:

    <!-- qa:skip reason="fetches from the deployer's repository on the lab host" -->
    ```bash
    sudo ./bootstrap/bootstrap.sh --machine <machine> --inspect-platform
    ```

    `<machine>` is the name of this lab host's host_vars file. Bootstrap
    prints the release's summary (services and their profiles, sockets, jobs,
    content) and exits 0, or prints every problem and exits 65
    ([exit code 65](troubleshoot.md#exit-code-65)).

## Deploy the release

1. Apply it:

    <!-- qa:skip reason="provisions the lab host" -->
    ```bash
    sudo ./bootstrap/bootstrap.sh --machine <machine> --tags platform,opa
    ```

    `<machine>` is the name of this lab host's host_vars file. The platform
    step validates the release again, builds it, pulls the training job
    images by digest, publishes the release and the content packages with
    their `DEPLOYED_FROM.json`, and switches `current`.

2. To update, set `ref` to the new release and apply it again. The new
   release is published next to the old ones and `current` switches; a
   published release is never extracted or built again, and a tag that now
   names another commit is refused.

3. To go back, set the ref of an earlier release and apply it again. Its
   directory is still there, so only `current` changes. Database migrations
   are forward-only: going back past a migration means restoring the
   database too.

A release with another platform name replaces the active platform only on
request: [How to replace the lab's platform](replace-platform.md).

## Verify the deployment

1. Check what is active:

    <!-- qa:run env=E2 -->
    ```bash
    cat /etc/pinka/platform/active.json
    ```

    `active.json` names the platform, its version, the manifest's SHA-256,
    the release and every content package with commit and tag. Each
    published directory carries `DEPLOYED_FROM.json` with its source, commit,
    tree, tag and signature check; the formats are in
    [Platform records](../reference/platform-records.md).
