# docker

Docker Engine with the Compose v2 plugin and buildx, for
the lab services that run as Compose projects.

**A Docker Engine that existed before pinka** (preflight's `components`)
is used as it is: pinka never installs, pins, holds, configures or
restarts it, and never writes its `/etc/docker/daemon.json`. Preflight has
already stopped (`needs_action`) where the lab could not run with it
unchanged: `docker.service` not running, the Compose or buildx plugin
missing, or CDI off on a host with a GPU feature.

**A Docker Engine pinka installs** (`tasks/install.yml`):

- the install record notes the component first;
- installed from the source of `ansible/component-sources.yml` (Docker's
  repositories on Ubuntu, Debian, Fedora and RHEL-compatible releases, the
  distribution's packages on openSUSE and Arch), pinned to
  `versions.lock.yml` and held;
- `/etc/docker/daemon.json` with only what such an install needs:
  `live-restore`, `data-root`, `selinux-enabled` on SELinux hosts, and for
  the GPU only on an engine older than 28.2 (a distribution's own package):
  `features.cdi`, or the NVIDIA runtime entry below 25.0. Docker 28.2 and
  later enable CDI by default (verified in moby's source of the pinned
  release). JSON has no comments and dockerd rejects unknown keys, so the
  managed header is an engine label (`org.pinka.managed=...`, also how
  preflight recognizes pinka's Docker). The file is validated with
  `dockerd --validate` before it is written, and Docker restarts only when
  it changed (`live-restore` keeps containers running).

The lab's other Docker settings are the same on every install and live in
its Compose projects (`compose` role): the `local` log driver with size
limits and the cgroup parent `pinka-services.slice` per service, and
networks with explicit subnets from `docker_address_pools` (`172.30.0.0/16`
in /24s, clear of Incus bridges, Tailscale and home LANs). The GPU arbiter
gives training containers the same cgroup parent and log limits.

`tasks/facts.yml` reads from the running engine, whoever installed it, how
containers reach the GPU (`pinka_docker_gpu_method`: `cdi` when `docker
info` lists CDI spec directories, `runtime` when it lists the NVIDIA runtime,
otherwise `none`; `pinka_docker_gpu_args`). The compose, gpu_arbiter,
lab_session, doctor and nvidia_container_toolkit roles include it.

Researchers are never added to `docker` (root-equivalent); admins listed in
`break_glass_docker` are, with a loud log line from `users_groups`, and the
install record notes the membership.

Coexistence with Incus is handled by the `firewall` role
(`pinka-docker-incus-compat.service`). Changing `docker_data_root` on an
existing host does not move the data; move it yourself with Docker stopped.

## Variables

See `meta/argument_specs.yml`.

## Feature flags

`gpu_inference` and `gpu_training` (from the host profile) decide whether
an older engine pinka installs needs a GPU setting in `daemon.json`.

## Idempotency

Packages are pinned and held; the configuration is rendered from a template
and Docker restarts only when it changed. A pre-existing Docker is only read.

## Check mode

Check mode verifies the source and the repository key, but cannot resolve
packages from a repository it did not really add. While Docker is not
installed (`dockerd` is missing when the play starts), the role ends after
its repository step in check mode.
