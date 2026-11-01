# nvidia_container_toolkit

NVIDIA Container Toolkit for GPU access from containers,
applied only where the lab uses the GPU (`gpu_inference` or `gpu_training`
after the switches):

- installed from NVIDIA's repositories (or the distribution's package on Arch)
  as `ansible/component-sources.yml` selects, pinned and held;
- **CDI first**: the toolkit's `nvidia-cdi-refresh.path` and
  `nvidia-cdi-refresh.service` (shipped with the toolkit since 1.18) write
  `/var/run/cdi/nvidia.yaml` whenever the driver or the toolkit changes; the
  role enables them, forces WSL mode on WSL2 with a drop-in, and generates the
  specification once if `nvidia-ctk cdi list` does not show
  `nvidia.com/gpu=all`. Services request `nvidia.com/gpu=all`;
- Docker releases without CDI (before 25.0) use the NVIDIA runtime instead.
  The `docker` role writes either into `daemon.json`; this role reads which
  one is active from that file and never edits it;
- a check runs `nvidia-smi` in the pinned CUDA base image, by digest, with the
  active method, and records the GPU name, driver, memory and compute
  capability with the method in `/var/lib/pinka/verify/gpu-docker.txt`.

## Variables

See `meta/argument_specs.yml`.

## Feature flags

`gpu_inference`, `gpu_training` (after the switches): the role is applied
only when one of them is on.

## Idempotency

Packages are pinned and held; the CDI specification is generated only when it
is missing; the check records stable `nvidia-smi` fields, so the record only
changes when the GPU or the driver changes. The check is skipped in check
mode.

## Check mode

Check mode verifies the source and the repository key, but cannot resolve
packages from a repository it did not really add. While the toolkit is not
installed (`nvidia-ctk` is missing when the play starts), the role ends after
its repository step in check mode.
