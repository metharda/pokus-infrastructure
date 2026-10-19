---
title: How to pin the vendor sources
type: how-to
audience: [developer]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: As a maintainer, pin the Zabbly and Tailscale repository keys and packages, the DCGM exporter image, the Incus base images, the GPU list of the NVIDIA driver and GitHub's SSH host keys with make lock-pins, review its output and land the change only after the full gate and CI pass.
---

# How to pin the vendor sources

`make lock-pins` reads the sources that `versions.lock.yml` cannot verify
from its own data, checks them and prints their pins in the lock's format.
Only a maintainer runs it, when pinning or upgrading these sources; users
and bootstrap never do. It reads public URLs and prints; it installs nothing
and changes no file. It covers:

- **The Zabbly and Tailscale repository keys.** Each key is downloaded from
  every URL the lock lists for it, and its primary key fingerprint is
  computed; every URL must carry the same single key. The output names where
  each fingerprint was taken from and what it was checked against: the
  fingerprint Zabbly publishes in its README on GitHub, and the key that
  keyserver.ubuntu.com returns for that fingerprint (which shows that the
  key was published there, not who owns it). Tailscale publishes its
  fingerprint nowhere apart from the key files, which the output says.
- **The Incus (Zabbly) and Tailscale packages** of every apt release the
  lock pins, from the repository layout in `ansible/component-sources.yml`:
  the repository's `InRelease` must carry a valid signature of the key above
  (checked with `gpgv` and that key alone), name the components the layout
  uses, and list the package index whose sha256 it states. The newest
  version of each package the component installs is read for x86_64 and
  aarch64. Zabbly builds each architecture separately, so the two versions
  differ; the lock holds one version per release, the x86_64 one, and the
  output says so.
- **The DCGM exporter image.** The newest release tag on nvcr.io, the digest
  of its image index and its platforms; the release's own
  `hack/versions.env` on GitHub must name the same exporter version, and its
  DCGM version is printed.
- **The Incus base images of the golden image pipeline.** The newest build
  of each pinned alias on images.linuxcontainers.org. Its fingerprint, the
  sha256 of the image's metadata and root file system together, is computed
  from the downloads and must equal the server's index. The server keeps only
  its newest builds, a few days, so pin them right before a lab's first
  golden image build; the lab then exports the base and imports it from
  there ([How golden images are built](../explanation/golden-images.md)).
- **The GPU list of the NVIDIA driver** (`nvidia_driver_gpus`). For every
  driver release the lock pins for an Ubuntu release, NVIDIA's
  `supported-gpus.json` of that release is read from Ubuntu's
  `nvidia-kernel-common-<branch>` package: the `restricted` index of
  `<codename>-updates` must carry a valid signature of the Ubuntu archive
  keyring (`/usr/share/keyrings/ubuntu-archive-keyring.gpg`, checked with
  `gpgv`), the package's sha256 must match the index, and `dpkg-deb` reads
  the file out of it. The device IDs the open kernel module supports (the
  `kernelopen` feature, without a `legacybranch`) must equal NVIDIA's table
  in the open-gpu-kernel-modules README at the release tag and the
  `Modaliases` of `nvidia-driver-<branch>-open`; the IDs the release supports
  with either module must equal the `Modaliases` of `nvidia-driver-<branch>`.
  Any difference refuses the section. Automatic driver installs use only the
  open-module list ([Bare-metal hosts](../providers/baremetal.md#which-gpus-get-a-driver)).
- **GitHub's SSH host keys.** From GitHub's meta API, GitHub's documentation
  and the server itself (`ssh-keyscan`): at least two must be reachable,
  every reachable source must agree, and each fingerprint is computed from
  its key and compared with the one published next to it.

A section that cannot be pinned prints `NOT PINNED` with the reason and no
pins; the run then exits 1, and the other sections are still printed.

!!! note "Prerequisites"
    - A development checkout of the repository; the target sets up its own
      Python environment.
    - `gpgv` (Debian and Ubuntu install it by default), and for GitHub's
      keys `ssh-keyscan` (package `openssh-client`).
    - For the GPU list: an Ubuntu machine, for the Ubuntu archive keyring
      (package `ubuntu-keyring`), and `dpkg-deb` (package `dpkg`), which
      reads the zstd-compressed packages.
    - Network access to `pkgs.zabbly.com`, `pkgs.tailscale.com`, `nvcr.io`,
      `images.linuxcontainers.org`, `archive.ubuntu.com`, `keyserver.ubuntu.com`,
      `raw.githubusercontent.com`, `api.github.com` and `github.com` on SSH
      port 22.

## Pin the sources

1. Run the target and keep its output:

    <!-- qa:skip reason="needs network access to the vendor sources" -->
    ```bash
    cd <checkout>
    make lock-pins > lock-pins.out
    echo "exit code: $?"
    ```

    `<checkout>` is the directory where you cloned the repository. Exit
    code 0 means every section was pinned; 1 means at least one says
    `NOT PINNED`.

2. Review the output: every fingerprint's sources, that the signed indexes
   and the image downloads checked out, the versions, the DCGM version, and
   the GPU list's cross-checks and its number of open-module device IDs.

3. Paste each block under the key its header names in `versions.lock.yml`
   (`repository_keys.keys`, `packages.incus`, `packages.tailscale`,
   `containers.images`, `incus_base_images.images`,
   `nvidia_driver_gpus.releases`, `github_ssh_host_keys`),
   set `verified_on` and `source` to the run, and remove the matching
   `verify` entries of `ansible/component-sources.yml` whose layout the run
   checked. Then run `make generate`.

4. Land the change only after `make lock-check lint test docs` and CI pass
   with the new pins.

## Verify the result

The lock check accepts the new pins and re-verifies the keys, the image
digest, that every pinned base image is still on its server, and the GPU
list (it downloads the pinned package again and recomputes the list):

<!-- qa:skip reason="needs network access to the vendor sources and the registries" -->
```bash
cd <checkout>
make lock-check
make verify-todos
```

`<checkout>` is the directory where you cloned the repository. Neither
target reports a line of the sections you pinned. A base image that is no
longer on its server is a warning: a lab that exported it keeps working, and
a new lab needs the pin renewed with `make lock-pins` before its first
golden image build.

The GPU list is checked offline too: every verified NVIDIA driver pin needs
the list of its release, so changing the driver pin without running
`make lock-pins` fails `make lock-check`. A pinned package that has left the
archive is a warning: the pin stays valid by its sha256, and you renew it
with `make lock-pins` when the driver pin changes.
