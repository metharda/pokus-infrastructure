---
title: How to update pinned versions
type: how-to
audience: [operator, developer]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Change a pin in versions.lock.yml, verify it, and regenerate everything that depends on it.
---

# How to update pinned versions

Every external artifact Pokus Infrastructure (pinka) installs (container images, binaries, packages,
repository keys, Python packages, Ansible collections) is pinned in
`infra/versions.lock.yml`. Nothing is updated automatically: you change a pin
by hand, verify it, and regenerate the files derived from the lock. The
current pins are listed in [Pinned versions](../reference/versions.md).

!!! note "Prerequisites"
    - A development checkout of the repository with `make setup` done.
    - Network access to the upstream source of the artifact you change.
    - `gpg` if you change a repository key (only to cross-check the fingerprint).

## Change a pin

1. Look at the procedure summary and the current state of the lock:

    <!-- qa:run env=E0 -->
    ```bash
    cd <checkout>/infra
    make lock-update
    make verify-todos
    ```

    `<checkout>` is the directory where you cloned the repository.
    `make verify-todos` lists every value that is still marked `TODO(verify)`.

2. Find the new release at its upstream source and verify it there; never take
   a value from a third-party page:

    | Artifact | Where to verify |
    | --- | --- |
    | Container image | The registry: the digest of the image index for the tag, and that it contains `linux/amd64` (and `linux/arm64`). |
    | Binary | The release page: download each archive, hash it with `sha256sum`, and compare with the checksum file of the release. |
    | Package | The repository index (apt `Packages`, rpm `repodata`) of each pinned release, for every architecture. |
    | Repository key | Download the key and compare its primary key fingerprint with the one the vendor documents. |
    | Python package | PyPI; then run `make bootstrap-lock` to recompute the hashes. |
    | Ansible collection | The collection's release tag and its `requires_ansible` in `meta/runtime.yml`; for `ansible_collections.fallback`, the built collection tarball attached to that GitHub release: download it and hash it with `sha256sum`. |

3. Edit `versions.lock.yml`: change the value, and update `verified_on` and
   `source` of the entry or section so that someone else can repeat the
   verification. Only stable releases are pinned; tags such as `latest` are
   rejected by the schema.

4. Regenerate the derived files. Recompile the Ansible environment's lock only
   if you changed `python.packages`:

    <!-- qa:run env=E0 -->
    ```bash
    make generate
    make bootstrap-lock
    ```

    `make generate` rewrites `bootstrap/lib/pins.sh`,
    `bootstrap/requirements.in`, `ansible/requirements.yml`,
    `ansible/group_vars/all/versions.yml` and this documentation's
    [Pinned versions](../reference/versions.md) page.

## Verify the change

1. Run the lock check. It validates the lock against its schema, fails on any
   remaining `TODO(verify)`, and re-verifies every pin over the network: each
   image digest exists in its registry, each binary has the pinned sha256, and
   each repository key has the pinned fingerprint:

    <!-- qa:run env=E0 -->
    ```bash
    make lock-check
    ```

    ```text title="Output"
    lock-check (network: containers, binaries, keys): 0 error(s), 0 warning(s)
    ```

    A warning that a tag "now points to" another digest is expected over time:
    the pin keeps using its digest until you change it.

2. Run the repository checks, then commit the lock together with the
   regenerated files:

    <!-- qa:run env=E0 -->
    ```bash
    make lint test docs
    git status --short
    ```
