# provider_wsl2

WSL2 provider (BUILD_PROMPT §3.1).

- Manages `/etc/wsl.conf`: systemd on, Windows drive automount and interop
  off, `appendWindowsPath` off, the machine name as host name. If the file
  changed, or systemd is not PID 1, the play stops with bootstrap signal 75:
  run `wsl --terminate <distro>` on the Windows side and re-run bootstrap.
- Never installs a Linux NVIDIA driver: the GPU comes from the Windows driver
  through `/dev/dxg`; the WSL GPU libraries in `/usr/lib/wsl/lib` are added to
  the dynamic linker path for the NVIDIA Container Toolkit.
- `.wslconfig` lives on Windows and is never changed by Pokus Infrastructure (pinka): `post_layout`
  renders a recommendation into
  `/srv/pinka/services/contrib/wslconfig.recommended` for the operator to copy.
  Doctor compares the effective memory, CPU count, swap and `/dev/kvm` with
  host_vars `expected`.
- Optional Windows helpers live in `contrib/windows/` and are never run by
  bootstrap (see `docs/providers/wsl2.md`).

## Variables

See `meta/argument_specs.yml` (entry points `main` and `post_layout`).

## Feature flags

`gpu_inference` (through `host_profile.gpu.present`).

## Idempotency

The signal fires only when `wsl.conf` changed or systemd is not PID 1; a
second run changes nothing.
