---
title: How to pin the Ansible collections
type: how-to
audience: [developer]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: As a maintainer, pin or upgrade the Ansible collections with make lock-collections, review its output and land the change only after the full gate and CI pass.
---

# How to pin the Ansible collections

`make lock-collections` asks Ansible Galaxy for the artifact of every
collection in `ansible_collections.collections` of `versions.lock.yml`,
downloads it, checks it and prints its pin in the lock's format. Only a
maintainer runs it, when pinning or upgrading collections; users and
bootstrap never do. It reads public URLs and prints; it installs nothing and
changes no file.

For each collection it checks that:

- the sha256 it computes from the download equals the one Galaxy publishes;
- the artifact's `MANIFEST.json` names that collection and version, its
  `FILES.json` matches the checksum in `MANIFEST.json`, every file is listed
  there with its checksum, and the tarball holds no unlisted file (the
  checks of `ansible-galaxy collection verify`);
- its members are directories, regular files and symbolic links whose
  target is relative and stays inside the collection, as `ansible-galaxy`
  extracts them (`community.general` links some test scripts this way); an
  absolute or escaping link, a hard link, a device or a FIFO is refused.
  `make setup` and bootstrap check every artifact with the same code before
  they install it, so a pin this target prints installs everywhere;
- every dependency the artifact declares is locked at a version that
  satisfies it.

It prints each artifact's URL, sha256, size, declared licenses and license
files.

!!! note "Prerequisites"
    - A development checkout of the repository; the target sets up its own
      Python environment.
    - Network access to `galaxy.ansible.com`.

## Pin the collections

1. Choose the versions. The lock follows the collections that the Ansible
   community package bundles (its `source` names the package release); edit
   the versions in `ansible_collections.collections` as described in
   [How to update pinned versions](update-versions.md).

2. Run the target:

    <!-- qa:skip reason="needs network access to Galaxy" -->
    ```bash
    cd <checkout>/infra
    make lock-collections LOCK_COLLECTIONS_ARGS="--with <namespace.name>==<version>"
    ```

    `<checkout>` is the directory where you cloned the repository, and
    `<namespace.name>` and `<version>` name a dependency that is not locked
    yet and its version. Leave `LOCK_COLLECTIONS_ARGS` out when every
    dependency is locked already;
    otherwise the run stops and names each dependency that is not, with the
    version range that needs it. Name its version with `--with`, once per
    dependency, taken from the same community package release.

3. Review the output: the versions, that every URL is on Galaxy, the sizes,
   and the licenses and license files of every artifact. A run that fails
   prints the reason and no pins.

4. Replace the `verified_on`, `collections` and `artifacts` entries under
   `ansible_collections` in `versions.lock.yml` with the output, set `source`
   to the run (date and Galaxy), and run `make generate`.

5. Land the change only after `make lint test docs` and CI pass with the new
   versions.

## Verify the result

The lock check accepts the new pins, and nothing in the tree names an
unverified collection pin:

<!-- qa:skip reason="needs network access to Galaxy and the registries" -->
```bash
cd <checkout>/infra
make lock-check
make verify-todos
```

`<checkout>` is the directory where you cloned the repository. Neither
command reports an `ansible_collections` line; `make setup` and bootstrap
then install the collections from the new artifacts and verify them.
