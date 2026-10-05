# provider_baremetal

Bare-metal provider (BUILD_PROMPT §3.2).

- **NVIDIA driver** (`install_nvidia_driver`): `auto` installs the pinned
  driver of the family (`component-sources.yml` `nvidia_driver`, versions from
  `versions.lock.yml`), blacklists nouveau and rebuilds the initramfs; `manual`
  expects a loaded driver and otherwise stops with signal 77; `skip` ignores
  the GPU. Community sources (RPM Fusion) need `--allow-community-repos`.
- **Secure Boot**: a pending MOK enrollment stops the play with signal 76
  (reboot, enroll the MOK at the blue MOK Manager screen, re-run). Secure Boot
  is never disabled.
- **Reboots**: a pending reboot (driver or kernel change) stops the play with
  signal 76. The play runs on the host itself, so bootstrap reboots only when
  `--allow-reboot` was given, after the play has ended.
- **Power policy**: `never_sleep` masks the sleep targets (and unmasks them if
  the policy changes later); `lab_session_inhibit` installs
  `pokus-sleep-inhibit.service`, which `pokus-lab.target` pulls in;
  `os_default` changes nothing.

The Incus storage device is handled by the incus role (BUILD_PROMPT §13.1).
`provider_vm` reuses the `nvidia`, `power` and `reboot` task files.

## Variables

See `meta/argument_specs.yml`.

## Feature flags

`gpu_inference` (through `host_profile.gpu`).

## Idempotency

The driver is installed only when no usable driver is loaded; masks and units
are declarative.
