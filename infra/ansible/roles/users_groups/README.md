# users_groups

Groups and accounts (BUILD_PROMPT §6.1, `docs/contracts.md`).

- Groups from `pinka_groups`.
- Infra accounts from `infra_accounts` (`pinka-build`, which runs platform
  builds) and service accounts from `service_accounts`: system users, shell
  `nologin`, home `/var/lib/pinka/<name>`, locked password, a primary group of
  the same name (the contract's sockets and password files use it) and
  exactly the listed supplementary groups.
- Researchers from `researchers`: members of `pinka` (admins also
  `pinka-admin`, with sudo through `/etc/sudoers.d/90-pinka-admin`, checked
  with `visudo -cf`). Incus's per-user group is added by the incus role.
  Non-admins and removed researchers are taken out of `pinka-admin`.
- `authorized_keys` comes exclusively from `users/<name>.pub`: a key removed
  from the file is removed from the host. Key files are never committed to
  the public project; a missing file leaves the account without SSH access
  and is reported.
- No researcher is in `docker` or `incus-admin`: the role removes them, except
  admins on the explicit `break_glass_docker` list (docker only), which is
  logged loudly.
- Removed researchers (`researchers_removed`) are locked: password locked,
  shell `nologin`, account expired. They are never deleted and their data is
  kept.

## Variables

See `meta/argument_specs.yml`; the account data lives in `group_vars/all/main.yml`.

## Feature flags

None.

## Idempotency

Declarative user and group modules; group removals run only for actual members.
