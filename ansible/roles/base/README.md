# base

Base system for every host:

- packages requested by logical name (`base_packages`) and mapped to real
  names per family in `vars/<family>.yml` (an empty name skips a package on
  that family), including python3-debian on the Debian family, which the
  `pkg` role's `deb822_repository` needs;
- system time zone UTC;
- persistent journald with size limits, in effect during the run, not from
  the next boot: journald restarted, and a journal that was volatile flushed
  to `/var/log/journal` with its tmpfiles.d group and modes, as a boot does;
- time sync: keeps the service the distribution uses (systemd-timesyncd or
  chronyd), installs the family default only if neither exists, and warns if
  the clock is not synchronized yet;
- automatic security updates per family: unattended-upgrades
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

Every task is declarative but the journal's: journald restarts only when
its drop-in changed, the flush runs only while `/var/log/journal` is absent,
and `systemd-tmpfiles` with a rotation only while it lacks the group
`systemd-journal` or the setgid bit (the run fails if tmpfiles.d does not
give them). The update configuration is rendered from templates. The time
sync check only reads state.
