---
title: How to plug a platform into the lab
type: how-to
audience: [operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Point the lab at a platform release and its content packages, check the release before it changes anything, deploy it and update it.
---

# How to plug a platform into the lab

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

## Configure the services

1. Give each service that names a configuration schema in the manifest its
   non-secret configuration in the host_vars file:

    <!-- qa:skip reason="example of a host_vars file" -->
    ```yaml
    platform_settings:
      <service>:
        <key>: <value>
    ```

    `<service>` is a service name from the manifest, `<key>` and `<value>`
    a setting its schema allows. A service without settings gets `{}`.
    The check of the next section refuses settings that do not match the
    schema, a service without a schema and an unknown service. Infra writes
    the configuration to `/etc/pinka/platform/config/<service>.json`,
    readable only by root and the service's account.

2. Put the platform's secrets in the vault
   (`ansible-vault edit group_vars/all/vault.yml`):

    <!-- qa:skip reason="example of vault content" -->
    ```yaml
    vault_postgres_passwords:
      <role>: "<password>"
    vault_platform_secrets:
      <service>:
        <name>: "<value>"
    ```

    `<role>` is a database role of the platform and `<password>` its
    password; `<service>` is a service name from the manifest, `<name>` one
    of its `secrets` and `<value>` the secret. The check of the next section
    lists every key the release needs under "Vault keys" and marks the
    missing ones: one password per database role (`<platform>_<service>` for
    each service with a database role, and `<platform>_migrator`,
    `<platform>_reader`, `<platform>_grafana`) and each secret the manifest
    names. A deployment with a missing key stops with exit code 77 before
    anything on the host changes.

## Check the release

1. Fetch, stage and validate the release without changing anything else:

    <!-- qa:skip reason="fetches from the deployer's repository on the lab host" -->
    ```bash
    sudo ./bootstrap/bootstrap.sh --machine <machine> --inspect-platform
    ```

    `<machine>` is the name of this lab host's host_vars file. Bootstrap
    prints the release's summary (services and their profiles, sockets, jobs,
    content, the vault keys it needs) and exits 0, or prints every problem
    and exits 65 ([exit code 65](troubleshoot.md#exit-code-65)).

## Deploy the release

1. Apply it:

    <!-- qa:skip reason="provisions the lab host" -->
    ```bash
    sudo ./bootstrap/bootstrap.sh --machine <machine>
    ```

    `<machine>` is the name of this lab host's host_vars file. The platform
    step validates the release again, builds it, pulls the training job
    images by digest, publishes the release and the content packages with
    their `DEPLOYED_FROM.json`, then renders everything the profiles grant:
    the accounts and groups, directories, configuration and secret files,
    the systemd units with their sockets, the database with its roles and
    the closed `restricted` schema, the Incus certificates, the signing key,
    `principals.json` and `endpoints.toml`, and the CLI link. Then it
    switches: a running release is drained (its `drain` hook) and stopped,
    the new release's `migrate` hook runs as `<platform>-migrate`, infra
    revokes and checks the `restricted` schema, and `current` switches. The
    roles after it pick up the policy bundle, the dashboards and the
    database dump from the same rendering; at the end bootstrap starts
    `pinka-platform.target` (the `reconcile` hook first, then the services)
    and waits for the `health` hook. A failed `migrate` hook or a forbidden
    grant on `restricted` ends bootstrap with the platform stopped; an
    unhealthy platform is reported and doctor warns.

2. To update, set `ref` to the new release and apply it again. The new
   release is published next to the old ones and `current` switches; a
   published release is never extracted or built again, and a tag that now
   names another commit is refused. The running release keeps running until
   the new one is built and published; only the switch stops it. A rerun
   with nothing changed neither stops nor migrates anything.

3. To go back, set the ref of an earlier release and apply it again. Its
   directory is still there, so only `current` changes. Database migrations
   are forward-only: going back past a migration means restoring the
   database too.

A release with another platform name replaces the active platform only on
request: [How to replace or purge a platform](replace-platform.md).

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

2. Check what the platform's services are told:

    <!-- qa:run env=E2 -->
    ```bash
    cat /etc/pinka/platform/principals.json /etc/pinka/platform/endpoints.toml
    ```

    `principals.json` lists every service and job with its account and UID
    and every socket with its path and allowed clients; `endpoints.toml`
    lists the addresses and paths infra provides.
