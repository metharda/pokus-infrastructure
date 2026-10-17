---
title: How to publish a release package
type: how-to
audience: [developer]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Build and check the release package of a vX.Y.Z tag with its pinned Ansible collections and NOTICE, let CI attach it to the GitHub release, and use a package for make setup without Galaxy.
---

# How to publish a release package

The release package of a tag `vX.Y.Z` is what an operator installs
from when a host should not need Git or Ansible Galaxy
([How to install from a release package](install-from-a-release-package.md)).
It holds the repository's tree at the tag (`git archive`) and `release/`: the
Ansible collection artifacts pinned in `ansible_collections.artifacts` of
`versions.lock.yml`, each checked against its pinned sha256 and size and by
the artifact check bootstrap runs; `NOTICE`, built from the license files
present in each artifact, the SPDX identifiers and REUSE annotations its
files name, and the licenses its metadata declares (a declared license that
no file carries is listed as such; no license text is added), with the
number of files of each artifact and every file that names no license; and
`RELEASE.json` with the tag, the commit and the repository's tree hash. The
package is reproducible: the same tag gives the same bytes. Packages are
never committed; `release/` is ignored by Git.

Between releases, the `release-package` workflow builds the package of the
pushed commit twice from Galaxy's artifacts, checks it, compares the two
builds byte for byte and prints the `NOTICE`, on every push that changes the
lock, `tools/lockcheck/`, the `Makefile` or the release workflows, and
weekly; it uploads nothing. A failure there blocks the next change like any
CI failure.

!!! note "Prerequisites"
    - A development checkout after `make setup`, with network access to
      `galaxy.ansible.com`.
    - Every collection pin verified: `make lock-collections-check` passes
      ([How to pin the Ansible collections](pin-ansible-collections.md)).

## Build and check a package locally

1. Build the package of the checked-out commit, as a test before tagging:

    <!-- qa:skip reason="downloads the collection artifacts from Galaxy" -->
    ```bash
    make release-package
    ```

    Without a tag the package is named
    `build/release/pokus-infrastructure-0.0.0+<commit>.tar.gz`, where
    `<commit>` is the first twelve characters of the commit; next to it is
    its `.sha256` file. `REF=<ref>` builds another commit.

2. Check it as bootstrap and lock-check would:

    <!-- qa:run env=E0 -->
    ```bash
    make release-package-check PACKAGE=build/release/pokus-infrastructure-0.0.0+<commit>.tar.gz
    ```

    `<commit>` is the same twelve characters as in step 1. The check
    unpacks the package safely and compares the tree hash of its
    files with `RELEASE.json`, every collection artifact with its pin and
    the artifact check, and `NOTICE` with one built from the artifacts. Read
    `release/NOTICE` in the unpacked tree once: every artifact must list the
    license texts you expect.

## Publish the package of a release tag

1. Tag the release commit and push the tag:

    <!-- qa:skip reason="creates and pushes a release tag" -->
    ```bash
    git tag -a v<version> -m "v<version>"
    git push origin v<version>
    ```

    `<version>` is the release, for example `1.2.0`.

2. The `release` workflow runs on the tag: it builds the package with
   `make release-package TAG=v<version>`, checks it with
   `make release-package-check`, creates the GitHub release of the tag if
   there is none, and uploads `pokus-infrastructure-<version>.tar.gz` and its
   `.sha256` file. It never replaces an asset that is already attached: a
   failed upload is fixed by deleting the asset by hand and re-running the
   workflow.

## Use a package for make setup without Galaxy

On a development machine that cannot reach Galaxy, `make setup` installs
the lint collections from a published package, a file or its `https` URL:

<!-- qa:run env=E0 -->
```bash
make setup COLLECTIONS_PACKAGE=pokus-infrastructure-<version>.tar.gz
```

`<version>` is the release whose package you downloaded. The package is
used only while Galaxy is unreachable. Every collection of
`ansible/requirements.yml` needs a verified pin in the checkout's
`versions.lock.yml` and gets the same sha256, size and artifact checks; a
collection without a verified pin, or one the package lacks, stops setup
and names it.

## Verify

1. Download the package and its `.sha256` from the release page of
   `v<version>`, and check both:

    <!-- qa:run env=E0 -->
    ```bash
    sha256sum -c pokus-infrastructure-<version>.tar.gz.sha256
    make release-package-check PACKAGE=pokus-infrastructure-<version>.tar.gz
    ```

    `sha256sum` prints `OK`, and the check passes.

2. Optionally build the same tag locally with
   `make release-package TAG=v<version>` and compare the two
   `.sha256` files: they are identical, because the package is reproducible.
