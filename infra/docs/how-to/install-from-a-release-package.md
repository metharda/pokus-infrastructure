---
title: How to install from a release package
type: how-to
audience: [operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Install or update a lab from the release package of an infra-vX.Y.Z tag, which carries the pinned Ansible collections, so bootstrap needs neither a Git clone nor Ansible Galaxy.
---

# How to install from a release package

Every release tag `infra-vX.Y.Z` has a release package: a tarball of the
infra tree at that tag, plus `release/` with the pinned Ansible collection
artifacts, their `NOTICE` and `RELEASE.json` (the tag, the commit and the
tree hash of `infra/`). Bootstrap runs from the unpacked package without
`.git` and installs the collections from it, offline. Use it when the host
cannot reach Ansible Galaxy, or when you want to install exactly a released
version.

!!! note "Prerequisites"
    - The package `pokus-infrastructure-<version>.tar.gz` and its checksum
      file `pokus-infrastructure-<version>.tar.gz.sha256`, from the release
      of the tag `infra-v<version>` on the repository's GitHub releases
      page. `<version>` is the release, for example `1.2.0`.
    - A candidate host that passes preflight
      ([How to check a host](check-a-host.md)), with `git` installed:
      bootstrap uses it to compute the package's tree hash.

## Unpack the package

1. Copy both files to the host, into the same directory, and check the
   package against its checksum:

    <!-- qa:run env=E1 -->
    ```bash
    sha256sum -c pokus-infrastructure-<version>.tar.gz.sha256
    ```

    `<version>` is the release. The line must end with `OK`. Any other
    answer means the package is not the published one: download it again,
    and do not use it.

2. Unpack it as root into a directory of its own:

    <!-- qa:run env=E1 -->
    ```bash
    sudo tar -xzf pokus-infrastructure-<version>.tar.gz -C /opt
    cd /opt/pokus-infrastructure-<version>
    ```

    The package has one top directory, `pokus-infrastructure-<version>/`.
    Its files must belong to root or to the account that runs `sudo`;
    bootstrap refuses any other owner.

## Run bootstrap from the package

1. Start the installer, or bootstrap with your host_vars file, exactly as
   from a clone ([How to install a lab with the installer TUI](install-with-the-tui.md),
   [How to install a lab from an answers file](install-without-the-tui.md)):

    <!-- qa:skip reason="provisions the lab host" -->
    ```bash
    sudo ./bootstrap/bootstrap.sh
    ```

    The files the installer writes (host_vars, the vault) are ignored by the
    tree hash, like in a clone. Bootstrap logs
    `release package infra-v<version>: repository commit …, infra tree …`.
    If any other file of the package was changed, added or removed, it
    stops with exit code 77 ("the package's files differ from its
    release"): unpack the package again. `--allow-dirty` overrides that and
    is recorded.

2. Keep the package. A restore of this lab needs the package of the
   recorded tag ([How to back up and restore the lab](backup-and-restore.md)),
   and the next update starts from a new package: unpack it next to the old
   one, copy `ansible/inventory/host_vars/<machine>.yml` and
   `ansible/group_vars/all/vault.yml` into it, and run bootstrap from the
   new directory. `<machine>` is the name of this lab host's host_vars file.

## Verify

1. Check the provenance bootstrap recorded:

    <!-- qa:run env=E2 -->
    ```bash
    sudo grep -E '"(infra_source|release_tag|repo_commit|infra_tree|infra_dirty)"' /etc/pinka/bootstrap-state.json
    ```

    `infra_source` is `"package"`, `release_tag` is `"infra-v<version>"`,
    `infra_dirty` is `false`, and `repo_commit` and `infra_tree` equal
    `commit` and `infra_tree` in `release/RELEASE.json` of the package.

2. Read the licenses of the bundled collections in `release/NOTICE`: the
   license texts each artifact carries, the SPDX identifiers its files
   name, and the licenses its metadata declares.
