# mac

Mandatory access control.

- **SELinux** (Fedora, RHEL-compatible, possibly openSUSE): never set to
  permissive or disabled. The role installs the management tools and the
  container policy, adds persistent `semanage fcontext` rules for the research
  space (`mac_selinux_fcontexts`) and applies them with `restorecon`.
  Container bind mounts use `:z`/`:Z` (docker role).
- **AppArmor** (Ubuntu, Debian, openSUSE): left as shipped; Docker and Incus
  bring their own profiles.

The detected system and mode are part of the environment fingerprint.

## Variables

See `meta/argument_specs.yml`.

## Feature flags

None.

## Idempotency

`restorecon` runs only when a rule changed (or with `mac_restorecon_always`).
