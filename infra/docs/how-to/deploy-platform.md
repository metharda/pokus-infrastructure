---
title: How to deploy the platform and the scenario catalog
type: how-to
audience: [operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Point the lab at your copy of the pokus-platform repository, deploy a platform and a catalog release from it, update to a new release and roll back.
---

# How to deploy the platform and the scenario catalog

The platform and the catalog are deployed from a root-only mirror of the
pokus-platform repository on the lab host (`/var/lib/pinka/git/potka.git`): bootstrap
fetches your copy of the repository and extracts one subdirectory at the ref
you choose, without `.git`, into `/srv/pinka/deploy/platform/<ref>/` or
`/srv/pinka/catalog/<ref>/`, and points `current` at it. Both are off until
you enable them, and both run on `full` and `lab` nodes only.

!!! note "Prerequisites"
    - A lab host provisioned by bootstrap (`node_role: full` or `lab`).
    - Your copy of the pokus-platform repository, reachable from the host by SSH or
      anonymous HTTPS.
    - On a lab node, a paired inference node
      ([How to run inference on a separate GPU node](pair-inference-node.md)).
    - The lab's private configuration (group_vars and host_vars files).

## Choose the source

1. Set the source in the lab's group_vars (all hosts of a lab use the same
   repository):

    <!-- qa:skip reason="example of a group_vars file" -->
    ```yaml
    pinka_source_repo: git@github.com:example-org/pokus-platform.git
    require_signed_tags: true
    pinka_allowed_signers:
      - "researcher1@example.com ssh-ed25519 <public key>"
    ```

    An `https://` URL needs no key but must allow anonymous reads; URLs with
    credentials are refused. For an SSH host other than GitHub, add its host
    keys as `known_hosts` lines in `pinka_source_known_hosts`. With
    `require_signed_tags: true` only tags with a valid SSH signature of an
    allowed signer are deployed.

2. With an SSH source, run bootstrap once with the catalog or the platform
   enabled (next section). It generates a deploy key on the host, prints its
   public key and stops at the fetch. Add the key to the repository as a
   read-only deploy key, then run bootstrap again.

## Deploy a release

1. Enable the components in the host_vars file and name the refs:

    <!-- qa:skip reason="example of a host_vars file" -->
    ```yaml
    catalog_enabled: true
    catalog_ref: catalog-v<version>
    platform_enabled: true
    platform_ref: platform-v<version>
    platform_settings: {}
    ```

    `<version>` is the release version in the component tag.
    A ref is a component tag (recommended; "deployed from a tag" in the
    platform's official check means exactly that), another tag, a branch or a
    full commit. `platform_settings` holds the services' settings by service
    (`orch`, `gw`, `exec`, `inject`, `verify`, `eval`); each file is checked
    against the release's own schema before it is installed.

2. Apply it:

    <!-- qa:skip reason="provisions the lab host" -->
    ```bash
    sudo ./bootstrap/bootstrap.sh --machine <machine> --tags catalog,platform,opa
    ```

    `<machine>` is the name of this lab host's host_vars file.
    The platform role creates the release's Python environment, writes the
    configuration, creates the signing key, runs the database migrations of
    a new commit, installs the units and starts them with the lab.

## Update or roll back

1. Set `platform_ref` (or `catalog_ref`) to the new release and apply it as
   above. Bootstrap fetches the repository again, extracts the new release
   next to the old ones and switches `current`; the running services restart
   at the end. A tag that moved to another commit is refused.

2. To roll back, set the ref of an earlier release and apply it again. Its
   directory is still there, so only `current` changes. Database migrations
   are not undone: check the platform's release notes before rolling back
   past a migration.

3. Old releases stay in `/srv/pinka/deploy/platform/` for provenance. Remove
   one by hand once no run refers to it.

## Verify the deployment

1. Check what is deployed:

    <!-- qa:run env=E2 -->
    ```bash
    cat /srv/pinka/deploy/platform/current/DEPLOYED_FROM.json
    sudo pinka doctor
    ```

    `DEPLOYED_FROM.json` names the source, the requested ref, the commit,
    the tag, the tree of the subdirectory and whether the tag was signed.
    Doctor's `deploy.provenance`, `systemd.hardening` and
    `sockets.permissions` checks cover the deployment.
