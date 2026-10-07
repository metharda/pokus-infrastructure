# docker

Docker Engine with the Compose v2 plugin and buildx (BUILD_PROMPT §7.6), for
the lab services that run as Compose projects:

- installed from the source of `ansible/component-sources.yml` (Docker's
  repositories on Ubuntu, Debian, Fedora and RHEL-compatible releases, the
  distribution's packages on openSUSE and Arch), pinned to
  `versions.lock.yml` and held;
- `/etc/docker/daemon.json`: `local` log driver with size limits,
  `live-restore`, `default-address-pools` (`172.30.0.0/16` in /24s by default,
  clear of Incus bridges, Tailscale and home LANs), `data-root`,
  `selinux-enabled` on SELinux hosts, and GPU access when the host has the
  `gpu_inference` feature: CDI (`features.cdi`) on Docker 25 and later,
  otherwise the NVIDIA runtime entry. JSON has no comments and dockerd rejects
  unknown keys, so the managed header is an engine label
  (`org.pokus.managed=...`, also how preflight recognizes the file). The file
  is validated with `dockerd --validate` before it is written, a replaced
  file is kept as a backup next to it, and only this role writes it. Docker
  restarts only when the file changed (`live-restore` keeps containers
  running);
- researchers are never added to `docker` (root-equivalent); admins listed in
  `break_glass_docker` are, with a loud log line from `users_groups`.

Coexistence with Incus is handled by the `firewall` role
(`pokus-docker-incus-compat.service`). Changing `docker_data_root` on an
existing host does not move the data; move it yourself with Docker stopped.

## Variables

See `meta/argument_specs.yml`.

## Feature flags

`gpu_inference` (from the host profile) selects the GPU access method.

## Idempotency

Packages are pinned and held; the configuration is rendered from a template
and Docker restarts only when it changed.

## Check mode

Check mode verifies the source and the repository key, but cannot resolve
packages from a repository it did not really add. While Docker is not
installed (`dockerd` is missing when the play starts), the role ends after
its repository step in check mode.
