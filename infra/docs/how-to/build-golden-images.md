---
title: How to build, test and export golden images
type: how-to
audience: [operator, scenario-author]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Pin the upstream base image, build the blueprints of images/, change a blueprint, handle a failed build or a leakage finding, and check the exports.
---

# How to build, test and export golden images

The `images` role builds every blueprint of `images/` that has no image yet,
runs its tests and the leakage lint, publishes it as `<blueprint>/<version>`
and exports it to `/srv/pinka/images/`. Bootstrap applies it on `full` and
`lab` hosts; this guide runs it on its own.
[How golden images are built](../explanation/golden-images.md) explains the
pipeline.

!!! note "Prerequisites"
    - A lab host provisioned at least once (Incus with the `build` project and
      the `pinka-build0` network), and an admin account on it (member of
      `pinka-admin`) in the infra directory bootstrap runs from.
    - Internet access from the lab host: the build downloads the base image,
      Ubuntu packages, the agent package and preloaded container images.
    - Room in the Incus pool for one build instance at a time
      (`incus_build_instance.root_size`) and in `/srv/pinka/images` for every
      exported image and the exported base.

## Pin the upstream base image

The role refuses to build while `versions.lock.yml` has no verified
fingerprint of the base image for the host's architecture
(`incus_base_images`). If the fingerprints there are still marked as
unverified, read the current ones from the image server:

<!-- qa:skip reason="needs access to images.linuxcontainers.org" -->
```bash
incus image list images:ubuntu/24.04/cloud --format json \
  | jq -r '.[] | select(.type == "container") | [.architecture, .fingerprint] | @tsv'
```

Write each architecture's fingerprint (all 64 characters) under
`incus_base_images.images.ubuntu-24.04.fingerprints`, with today's date in
`verified_on`, as described in [How to update pinned versions](update-versions.md).
Changing the pin later changes the base of every blueprint: bump the
blueprints' versions at the same time.

## Build the images

Run bootstrap with only the `images` tag:

<!-- qa:skip reason="builds images on the lab host" -->
```bash
sudo ./bootstrap/bootstrap.sh --machine <machine> --tags images
```

`<machine>` is the name of this lab host's host_vars file. The first run
builds `_base` and then the other blueprints, each in its own instance
`build-<blueprint>` in the project `build`. A second run changes nothing.

## Change a blueprint

1. Edit the blueprint's files in `images/<blueprint>/` and raise `version`
   in its `image.yaml`. A blueprint that starts from a changed blueprint
   (`base: {blueprint: ...}`) needs a new version too.
2. Check the recipe:

    <!-- qa:run env=E1 -->
    ```bash
    make lint-schemas lint-ansible test
    ```

3. Commit the change and run bootstrap with `--tags images` again. The new
   version gets its own alias and export; the old version's image stays in
   the image store until you delete it.

To build the same version again (for example after a broken export),
set `images_rebuild: true` and `images_blueprints: [<blueprint>]` in
`ansible/inventory/host_vars/<machine>.yml`, run bootstrap with
`--tags images`, and remove both settings again. `<blueprint>` is the
blueprint's directory name.

## When a build fails

The failed build's instance stays in the project `build`. Open a shell in it:

<!-- qa:skip reason="needs a failed build on the lab host" -->
```bash
sudo incus exec --project build build-<blueprint> -- bash
```

`<blueprint>` is the blueprint's name without underscores (`build-base` for
`_base`). The output of the provisioning run is in
`/var/lib/pinka/image-builds/<blueprint>/provision.log`. The next run deletes
the instance and builds again.

A leakage finding is printed as `leakage: <kind> <path>: <token>`. Remove what
leaks from the recipe. If the token is a harmless word that a package ships
(for example a new `default`-like word), add a benign word to
`images_leakage_allow` in the host_vars file, as narrow as possible:

<!-- qa:skip reason="example of a host_vars entry" -->
```yaml
images_leakage_allow:
  - {match: "(?i)default"}
  - {match: "(?i)scenarios?", path: "etc/redis/redis\\.conf"}
  - {match: "<word>", path: "<path>"}
```

`<word>` is a regular expression of the benign word and `<path>` one of the
file's path below the root, without the leading `/`. Keep the two default
rules: the setting replaces the whole list.

## Verify the images

List the golden images with their properties:

<!-- qa:run env=E2 -->
```bash
sudo incus image list --format json \
  | jq -r '.[] | select(.aliases | length > 0) | [.aliases[0].name, .fingerprint[:12], .properties["pinka.built_at"]] | @tsv'
```

Every blueprint appears with its version, a fingerprint and the build time.
Check an export against its fingerprint and its sums:

<!-- qa:run env=E2 -->
```bash
cd /srv/pinka/images/<blueprint>/<version>
sudo /usr/local/libexec/pinka/image-files verify --dir . --fingerprint "$(sudo jq -r .fingerprint build.json)"
sudo sha256sum -c SHA256SUMS
```

`<blueprint>` and `<version>` name the export. The helper prints
`"ok": true`, and `sha256sum` prints `OK` for every file.

Related: [How golden images are built](../explanation/golden-images.md),
[ADR-0012](../adr/0012-leakage-lint.md).
