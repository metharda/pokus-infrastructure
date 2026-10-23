# base

Base system for every host (BUILD_PROMPT §7.2):

- packages requested by logical name (`base_packages`) and mapped to real
  names per family in `vars/<family>.yml` (an empty name skips a package on
  that family), including python3-debian on the Debian family, which the
  `pkg` role's `deb822_repository` needs;
- system time zone UTC;
- persistent journald with size limits;
- time sync: keeps the service the distribution uses (systemd-timesyncd or
  chronyd), installs the family default only if neither exists, and warns if
  the clock is not synchronized yet;
- automatic security updates per family (BUILD_PROMPT §19.5): unattended-upgrades
  (Debian family), dnf-automatic (Red Hat family), a `zypper patch --category
  security` timer (SUSE family); never an automatic reboot; packages pinned by
  Pokus Infrastructure (pinka) and, on bare metal, the kernel are excluded; none on rolling releases;
- kernel settings in `/etc/sysctl.d/60-pinka.conf` (inotify limits, IPv4
  forwarding for Docker and Incus).

## Variables

See `meta/argument_specs.yml` and `defaults/main.yml`.

## Feature flags

None.

## Idempotency

Every task is declarative; the update configuration is rendered from
templates. The time sync check only reads state.
